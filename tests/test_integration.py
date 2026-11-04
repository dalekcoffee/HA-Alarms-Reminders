"""End-to-end tests: voice tools, scheduling, ringing, UI forms."""

from datetime import datetime, timedelta

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.llm import LLMContext, ToolInput
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.alarms_reminders import llm as alarm_llm
from custom_components.alarms_reminders.const import (
    DOMAIN,
    EVENT_RINGING,
    OPT_LIGHT_RAMP_MINUTES,
    OPT_NOTIFY_TARGETS,
    OPT_RAMP_MINUTES,
)

NOW = datetime(2026, 11, 4, 22, 0, 0)  # a Wednesday evening


@pytest.fixture
async def setup(hass: HomeAssistant, freezer):
    """Set up a bedroom voice device and the integration."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(NOW.replace(tzinfo=dt_util.get_default_time_zone()))
    await async_setup_component(hass, "homeassistant", {})

    calls = {
        "announce": async_mock_service(hass, "assist_satellite", "announce"),
        "volume": async_mock_service(hass, "media_player", "volume_set"),
        "stop": async_mock_service(hass, "media_player", "media_stop"),
        "light": async_mock_service(hass, "light", "turn_on"),
        "notify": async_mock_service(hass, "notify", "mobile_app_phone"),
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            OPT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            OPT_RAMP_MINUTES: 0,
            OPT_LIGHT_RAMP_MINUTES: 0,
        },
    )
    entry.add_to_hass(hass)

    area = ar.async_get(hass).async_create("Bedroom")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "dot")}, name="Bedroom Dot"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    ent_reg = er.async_get(hass)
    sat = ent_reg.async_get_or_create("assist_satellite", "test", "sat", device_id=device.id)
    player = ent_reg.async_get_or_create("media_player", "test", "player", device_id=device.id)
    light = ent_reg.async_get_or_create("light", "test", "lamp")
    ent_reg.async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(sat.entity_id, "idle")
    hass.states.async_set(player.entity_id, "idle", {"volume_level": 0.3})
    hass.states.async_set(light.entity_id, "off")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return {
        "entry": entry,
        "device": device,
        "satellite": sat.entity_id,
        "player": player.entity_id,
        "light": light.entity_id,
        "calls": calls,
    }


async def _tool(hass, tool, device_id=None, **args):
    ctx = LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=device_id)
    return await tool.async_call(hass, ToolInput(tool.name, args), ctx)


async def test_tools_are_offered_to_llm(hass, setup):
    """The Assist API lists our tools with the domain prefix."""
    from homeassistant.components.llm import async_get_tools

    await async_setup_component(hass, "llm", {})
    ctx = LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    result = await async_get_tools(hass, ctx, "assist")
    names = {tool.name for tool in result.tools}
    assert f"{DOMAIN}__set_alarm" in names
    assert f"{DOMAIN}__set_reminder" in names
    assert "Only make an alarm or reminder recurring" in result.prompt


async def test_one_time_vs_recurring(hass, setup):
    """No repeat = one time; 'every weekday' = recurring; room is mentioned."""
    device_id = setup["device"].id
    once = await _tool(hass, alarm_llm.SetAlarmTool(), device_id, time="07:00")
    await hass.async_block_till_done()
    assert once["success"]
    assert once["speech"] == "One-time alarm set for 7:00 AM tomorrow in the Bedroom."

    weekly = await _tool(hass, alarm_llm.SetAlarmTool(), device_id, time="07:00", repeat="every weekday", wake_up=True)
    await hass.async_block_till_done()
    assert weekly["speech"].startswith("Recurring alarm set for 7:00 AM every weekday in the Bedroom.")

    entry = setup["entry"]
    assert len(entry.subentries) == 2
    switches = [s for s in hass.states.async_all("switch")]
    assert len(switches) == 2

    listed = await _tool(hass, alarm_llm.ListTool(), kind="alarm")
    assert "You have 2 alarms" in listed["speech"]


async def test_reminder_asks_for_time(hass, setup):
    """A reminder without a time asks for one."""
    result = await _tool(hass, alarm_llm.SetReminderTool(), setup["device"].id, message="call the vet", date="2026-11-06")
    assert not result["success"]
    assert result["needs"] == "time"
    assert result["speech"] == "What time Friday should I remind you?"
    assert not setup["entry"].subentries


async def test_alarm_rings_and_stops(hass, setup, freezer):
    """At the time, it rings on the device it was set from, lights come on, stop works."""
    device_id = setup["device"].id
    events = []
    hass.bus.async_listen(EVENT_RINGING, events.append)
    await _tool(hass, alarm_llm.SetAlarmTool(), device_id, in_minutes=5, wake_up=True)
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    for _ in range(5):
        await hass.async_block_till_done()

    assert len(events) == 1
    manager = setup["entry"].runtime_data
    assert manager.ringing
    assert hass.states.get("binary_sensor.alarms_reminders_alarm_ringing").state == "on"
    announce = setup["calls"]["announce"]
    assert announce and announce[0].data["media_id"].endswith("/alarms_reminders/car_alarm.wav")
    assert announce[0].data["entity_id"] == [setup["satellite"]] or announce[0].data["entity_id"] == setup["satellite"]
    lights = setup["calls"]["light"]
    assert lights and lights[0].data["brightness_pct"] == 100
    assert lights[0].data["color_temp_kelvin"] == 6500

    stopped = await _tool(hass, alarm_llm.StopTool())
    await hass.async_block_till_done()
    assert stopped["speech"] == "Alarm stopped."
    assert not manager.ringing
    # one-time alarm is gone; volume restored
    assert not setup["entry"].subentries
    assert setup["calls"]["volume"][-1].data["volume_level"] == 0.3


async def test_snooze_creates_one_time_alarm(hass, setup, freezer):
    """Snooze stops ringing and adds a snooze alarm."""
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, in_minutes=1)
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    result = await _tool(hass, alarm_llm.SnoozeTool())
    await hass.async_block_till_done()
    assert result["success"]
    subs = list(setup["entry"].subentries.values())
    assert len(subs) == 1
    assert subs[0].title.startswith("Snooze")


async def test_skip_next_weekday(hass, setup):
    """Skip keeps the alarm and moves to the next day."""
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, time="07:00", repeat="weekdays")
    await hass.async_block_till_done()
    result = await _tool(hass, alarm_llm.SkipTool(), target="7am")
    await hass.async_block_till_done()
    assert result["speech"] == "Skipped the 7:00 AM alarm tomorrow. It'll ring again Friday at 7:00 AM."
    assert len(setup["entry"].subentries) == 1


async def test_reminder_delivered(hass, setup, freezer):
    """Reminders are spoken and sent to the phone."""
    result = await _tool(hass, alarm_llm.SetReminderTool(), setup["device"].id, message="take the bins out", time="22:30")
    assert result["speech"] == "One-time reminder set to take the bins out for 10:30 PM today in the Bedroom."
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    assert setup["calls"]["notify"][0].data["message"] == "take the bins out"
    assert setup["calls"]["announce"][0].data["message"] == "Reminder: take the bins out"


async def test_change_and_cancel(hass, setup):
    """Change time; cancel by label."""
    device_id = setup["device"].id
    await _tool(hass, alarm_llm.SetAlarmTool(), device_id, time="06:00", repeat="weekends", label="gym")
    await hass.async_block_till_done()
    changed = await _tool(hass, alarm_llm.ChangeTool(), target="gym", time="06:30")
    await hass.async_block_till_done()
    assert changed["speech"] == "Updated. It's now a recurring alarm for 6:30 AM every weekend in the Bedroom."
    cancelled = await _tool(hass, alarm_llm.CancelTool(), target="gym")
    await hass.async_block_till_done()
    assert cancelled["success"]
    assert not setup["entry"].subentries
    assert not hass.states.async_all("switch")


async def test_ui_add_and_edit_alarm(hass, setup):
    """Alarms can be added and edited from the integration page."""
    entry = setup["entry"]
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "alarm"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"time": "06:45:00", "repeat": ["0", "2"], "satellite": setup["satellite"], "wake": True, "enabled": True, "label": "Swim"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sid = next(iter(entry.subentries))
    assert entry.subentries[sid].title == "Alarm 6:45 AM, Monday and Wednesday – Swim"
    assert hass.states.async_all("switch")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "alarm"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sid}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"time": "07:15:00", "repeat": ["0", "2"], "satellite": setup["satellite"], "wake": True, "enabled": True, "label": "Swim", "skip_next": True},
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    item = entry.runtime_data.items[sid]
    assert item.at.hour == 7 and item.at.minute == 15
    assert len(item.skip) == 1
    assert item.media_player == setup["player"]


async def test_switch_turns_alarm_off(hass, setup):
    """Turning the switch off keeps it but stops it ringing."""
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, time="07:00", repeat="daily")
    await hass.async_block_till_done()
    switch = hass.states.async_all("switch")[0].entity_id
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
    assert hass.states.get("sensor.alarms_reminders_next_alarm").state == "unknown"


async def test_built_in_sounds_written(hass, setup, media_dir):
    """Built-in sounds are created in the media folder."""
    folder = media_dir / "alarms_reminders"
    assert (folder / "car_alarm.wav").stat().st_size > 10000
    assert (folder / "beeps.wav").exists()


async def test_local_stop_sentence(hass, setup, freezer):
    """'Stop' works locally (no LLM) while an alarm rings."""
    from homeassistant.components import conversation

    await async_setup_component(hass, "conversation", {})
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, in_minutes=1)
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    manager = setup["entry"].runtime_data
    assert manager.ringing

    result = await conversation.async_converse(hass, "stop", None, None, language="en")
    await hass.async_block_till_done()
    assert result.response.speech["plain"]["speech"] == "Alarm stopped."
    assert not manager.ringing

    result = await conversation.async_converse(hass, "snooze the alarm", None, None, language="en")
    assert result.response.speech["plain"]["speech"] == "No alarm is ringing."


async def test_test_button_rings_without_lights(hass, setup, monkeypatch):
    """Each alarm has a Test button that rings it now, without lights, and keeps it."""
    manager = setup["entry"].runtime_data

    async def no_length(item):
        return None  # skip the executor read so the sound starts within block_till_done

    monkeypatch.setattr(manager, "async_sound_duration", no_length)
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, time="07:00", repeat="weekdays", wake_up=True)
    await hass.async_block_till_done()
    button = next(
        s.entity_id for s in hass.states.async_all("button") if s.entity_id.startswith("button.test_")
    )
    events = []
    hass.bus.async_listen(EVENT_RINGING, events.append)
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    manager = setup["entry"].runtime_data
    assert manager.ringing
    assert events[0].data["test"] is True
    assert setup["calls"]["announce"][0].data["media_id"].endswith("car_alarm.wav")
    assert not setup["calls"]["light"]
    assert setup["calls"]["volume"][0].data["volume_level"] == 0.7  # no ramp in a test
    await manager.async_stop_ringing()
    assert len(setup["entry"].subentries) == 1


async def test_test_button_speaks_reminder(hass, setup):
    """A reminder's Test button speaks it."""
    await _tool(hass, alarm_llm.SetReminderTool(), setup["device"].id, message="feed the cat", time="08:00")
    await hass.async_block_till_done()
    button = next(
        s.entity_id for s in hass.states.async_all("button") if s.entity_id.startswith("button.test_")
    )
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert setup["calls"]["announce"][-1].data["message"] == "Reminder: feed the cat"


async def test_options_test_sound(hass, setup):
    """Configure › Test a sound plays it once and stays open."""
    entry = setup["entry"]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "test_sound"})
    assert result["step_id"] == "test_sound"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sound": "beeps.wav", "satellite": setup["satellite"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["played"] == "Played Beeps. "
    assert setup["calls"]["announce"][-1].data["media_id"].endswith("/alarms_reminders/beeps.wav")


@pytest.mark.parametrize(
    ("sentence", "speech"),
    [
        ("remind me to check the oven in 15 minutes", "One-time reminder set to check the oven for 10:15 PM today in the Bedroom."),
        ("remind me in 2 hours to call mom", "One-time reminder set to call mom for 12:00 AM tomorrow in the Bedroom."),
        ("in half an hour remind me to stretch", "One-time reminder set to stretch for 10:30 PM today in the Bedroom."),
        ("remind me to check the oven in fifteen minutes", "One-time reminder set to check the oven for 10:15 PM today in the Bedroom."),
    ],
)
async def test_local_remind_me_in(hass, setup, sentence, speech):
    """'Remind me ... in N minutes' makes a reminder, not HA's delayed-command timer."""
    from homeassistant.components import conversation

    await async_setup_component(hass, "conversation", {})
    result = await conversation.async_converse(
        hass, sentence, None, None, language="en", device_id=setup["device"].id
    )
    await hass.async_block_till_done()
    assert result.response.speech["plain"]["speech"] == speech
    assert len(setup["entry"].subentries) == 1


async def test_local_wake_me_in(hass, setup):
    """'Wake me up in 20 minutes' makes a one-time alarm."""
    from homeassistant.components import conversation

    await async_setup_component(hass, "conversation", {})
    result = await conversation.async_converse(
        hass, "wake me up in 20 minutes", None, None, language="en", device_id=setup["device"].id
    )
    assert result.response.speech["plain"]["speech"] == "One-time alarm set for 10:20 PM today in the Bedroom."


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


async def _ring_now(hass, setup, freezer):
    await _tool(hass, alarm_llm.SetAlarmTool(), setup["device"].id, in_minutes=1)
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_cut_short_on_device_stops_alarm(hass, setup, freezer, monkeypatch):
    """If the sound is cut short (EchoLocal "stop" or action button), the alarm stops."""
    from custom_components.alarms_reminders import manager as manager_mod

    clock = _Clock()
    monkeypatch.setattr(manager_mod, "monotonic_time", clock)
    manager = setup["entry"].runtime_data
    plays = []

    async def short_play(item, media):
        plays.append(media)
        clock.now += 3  # car_alarm.wav is ~8 s; device stopped it after 3 s

    monkeypatch.setattr(manager, "async_play", short_play)
    stopped = []
    hass.bus.async_listen("alarms_reminders_stopped", stopped.append)
    await _ring_now(hass, setup, freezer)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(plays) == 1
    assert not manager.ringing
    assert stopped[0].data["reason"] == "dismissed"
    # "Okay Nabu, stop" right after still gets a sensible answer
    assert (await _tool(hass, alarm_llm.StopTool()))["speech"] == "Alarm stopped."


async def test_full_play_keeps_ringing(hass, setup, freezer, monkeypatch):
    """A sound that plays to the end repeats."""
    from custom_components.alarms_reminders import manager as manager_mod

    clock = _Clock()
    monkeypatch.setattr(manager_mod, "monotonic_time", clock)
    manager = setup["entry"].runtime_data
    plays = []
    gate = __import__("asyncio").Event()

    async def full_play(item, media):
        plays.append(media)
        clock.now += 9
        if len(plays) >= 2:
            await gate.wait()

    monkeypatch.setattr(manager, "async_play", full_play)
    await _ring_now(hass, setup, freezer)
    for _ in range(3):
        await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    for _ in range(3):
        await hass.async_block_till_done()
    assert manager.ringing
    await manager.async_stop_ringing()
    gate.set()


async def test_wake_word_stops_alarm(hass, setup, freezer):
    """The device starting to listen (wake word / action button) stops the alarm."""
    manager = setup["entry"].runtime_data
    await _ring_now(hass, setup, freezer)
    assert manager.ringing
    hass.states.async_set(setup["satellite"], "listening")
    await hass.async_block_till_done()
    assert not manager.ringing

