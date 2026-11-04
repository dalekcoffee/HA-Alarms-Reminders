"""Local sentences for stopping and snoozing, handled without the LLM.

"Stop the alarm" and "snooze the alarm" always work. While an alarm is
ringing, a bare "stop" or "snooze" works too. These need "Prefer handling
commands locally" on in the voice pipeline; otherwise the LLM's stop and
snooze tools handle them.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import actions
from .const import KIND_ALARM, KIND_REMINDER, SIGNAL_UPDATED
from .manager import AlarmManager

_LOGGER = logging.getLogger(__name__)

STOP_SENTENCES = [
    "(stop|cancel|dismiss|turn off|shut off|silence) [the] alarm[s]",
    "alarm (off|stop)",
]
SNOOZE_SENTENCES = ["snooze [the] alarm[s]", "snooze [the] alarm[s] for {minutes} minute[s]"]
RINGING_STOP = ["stop", "stop it", "shut up", "be quiet", "enough", "i'm up", "i am up", "okay i'm up"]
# Home Assistant's built-in "<command> in N minutes" (a delayed command
# timer) would otherwise catch these before they reach the LLM, and set a
# hidden timer instead of a reminder. Sentence triggers are checked first.
_IN = "(in|after)"
_SPAN = (
    "({minutes} minute[s]|{hours} hour[s]|{hours} hour[s] [and] {minutes} minute[s]"
    "|an hour|half an hour|a minute)"
)
REMINDER_IN = [
    f"remind me [to] {{task}} {_IN} {_SPAN}",
    f"remind me {_IN} {_SPAN} [to] {{task}}",
    f"{_IN} {_SPAN} remind me [to] {{task}}",
]
ALARM_IN = [
    f"wake me [up] {_IN} {_SPAN}",
    f"set [an|a] alarm {_IN} {_SPAN}",
    f"{_IN} {_SPAN} wake me [up]",
]

_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
    "twenty five": 25, "twenty-five": 25, "half": 0.5,
}


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip().lower()
    try:
        return float(text)
    except ValueError:
        return _NUMBERS.get(text)


def _span_minutes(user_text: str, result: Any) -> float | None:
    """Minutes from '{hours} hours and {minutes} minutes', 'an hour', 'half an hour'."""
    lowered = user_text.lower()
    if "half an hour" in lowered:
        return 30
    entities = result.entities if result is not None else {}
    hours = _number(entities["hours"].text) if "hours" in entities else None
    minutes = _number(entities["minutes"].text) if "minutes" in entities else None
    if hours is None and minutes is None:
        if "an hour" in lowered:
            return 60
        if "a minute" in lowered:
            return 1
        return None
    if ("hours" in entities and hours is None) or ("minutes" in entities and minutes is None):
        return None
    return (hours or 0) * 60 + (minutes or 0)


def _task(result: Any) -> str:
    entity = result.entities.get("task") if result is not None else None
    return entity.text.strip() if entity else ""


def _reminder_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        minutes = _span_minutes(user_input.text, result)
        if minutes is None:
            return "Sorry, I didn't catch how long. Try saying it in minutes."
        return actions.set_item(
            manager,
            KIND_REMINDER,
            in_minutes=minutes,
            message=_task(result),
            device_id=getattr(user_input, "device_id", None),
        )["speech"]

    return on_sentence


def _alarm_in_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        minutes = _span_minutes(user_input.text, result)
        if minutes is None:
            return "Sorry, I didn't catch how long. Try saying it in minutes."
        return actions.set_item(
            manager,
            KIND_ALARM,
            in_minutes=minutes,
            device_id=getattr(user_input, "device_id", None),
        )["speech"]

    return on_sentence


RINGING_SNOOZE = ["snooze", "snooze it", "five more minutes", "snooze for {minutes} minute[s]"]


def _register(hass: HomeAssistant, sentences: list[str], handler: Any) -> CALLBACK_TYPE:
    # Not a public API, but it's how conversation triggers register sentences.
    from homeassistant.components.conversation.agent_manager import get_agent_manager

    manager = get_agent_manager(hass)
    try:
        from homeassistant.components.conversation.trigger import TriggerDetails
    except ImportError:  # Home Assistant 2026.10 and later
        return manager.register_trigger(sentences, handler)
    return manager.register_trigger(TriggerDetails(sentences, handler))


def _minutes(result: Any) -> Any:
    entity = result.entities.get("minutes") if result is not None else None
    if entity is None:
        return None
    return entity.value if entity.value not in (None, "") else entity.text


def _stop_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        return (await actions.stop(manager))["speech"]

    return on_sentence


def _snooze_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        return (await actions.snooze(manager, _minutes(result)))["speech"]

    return on_sentence


def async_setup_voice(hass: HomeAssistant, manager: AlarmManager) -> CALLBACK_TYPE:
    """Register sentences. Returns a callback that removes them."""
    removers: list[CALLBACK_TYPE] = []
    ringing: list[CALLBACK_TYPE] = []
    try:
        removers.append(_register(hass, STOP_SENTENCES, _stop_handler(manager)))
        removers.append(_register(hass, SNOOZE_SENTENCES, _snooze_handler(manager)))
        removers.append(_register(hass, REMINDER_IN, _reminder_handler(manager)))
        removers.append(_register(hass, ALARM_IN, _alarm_in_handler(manager)))
    except Exception:
        _LOGGER.warning(
            "Could not register local stop/snooze sentences; your conversation "
            "agent can still stop alarms",
            exc_info=True,
        )
        return lambda: None

    @callback
    def _update() -> None:
        if manager.ringing and not ringing:
            ringing.append(_register(hass, RINGING_STOP, _stop_handler(manager)))
            ringing.append(_register(hass, RINGING_SNOOZE, _snooze_handler(manager)))
        elif not manager.ringing and ringing:
            while ringing:
                ringing.pop()()

    removers.append(
        async_dispatcher_connect(hass, f"{SIGNAL_UPDATED}_{manager.entry.entry_id}", _update)
    )

    @callback
    def _remove() -> None:
        for remove in removers + ringing:
            remove()

    return _remove
