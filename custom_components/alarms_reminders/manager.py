"""Scheduling and ringing for alarms and reminders."""

from __future__ import annotations

import asyncio
import logging
import time as monotonic_time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from . import sounds
from .const import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_KELVIN,
    DEFAULT_LIGHT_RAMP_MINUTES,
    DEFAULT_RAMP_MINUTES,
    DEFAULT_RING_MINUTES,
    DEFAULT_SNOOZE_MINUTES,
    DEFAULT_SOUND,
    DEFAULT_START_VOLUME,
    DEFAULT_VOLUME,
    EVENT_REMINDER,
    EVENT_RINGING,
    EVENT_STOPPED,
    KIND_ALARM,
    KIND_REMINDER,
    MISSED_GRACE_MINUTES,
    OPT_BRIGHTNESS,
    OPT_DEFAULT_SATELLITE,
    OPT_DEFAULT_SOUND,
    OPT_KELVIN,
    OPT_LIGHT_RAMP_MINUTES,
    OPT_NOTIFY_TARGETS,
    OPT_RAMP_MINUTES,
    OPT_RING_MINUTES,
    OPT_SNOOZE_MINUTES,
    OPT_START_VOLUME,
    OPT_VOLUME,
    OPT_WAKE_LIGHTS,
    SIGNAL_NEW_ITEM,
    SIGNAL_UPDATED,
)
from .model import Item, prune_skips

_LOGGER = logging.getLogger(__name__)

VOLUME_STEP_SECONDS = 15
TEST_SECONDS = 30
LIGHT_STEP_SECONDS = 30


@dataclass
class Placement:
    """Where an alarm rings."""

    satellite: str | None
    media_player: str | None
    area_id: str | None
    area_name: str | None


class AlarmManager:
    """Owns every alarm and reminder of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.entry = entry
        self.items: dict[str, Item] = {}
        self.next_fires: dict[str, datetime] = {}
        self.rings: dict[str, Ring] = {}
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._known: set[str] = set()
        self._writing = False

    # --- lifecycle --------------------------------------------------------

    @property
    def options(self) -> MappingProxyType[str, Any]:
        """Integration-wide settings."""
        return self.entry.options

    def opt(self, key: str, default: Any) -> Any:
        """Read a setting."""
        value = self.entry.options.get(key)
        return default if value is None else value

    async def async_start(self) -> None:
        """Load items, ring anything missed during a restart, and schedule."""
        self._load()
        self._known = set(self.items)
        now = dt_util.now()
        today = now.date()
        late: list[str] = []
        for sid, item in list(self.items.items()):
            if prune_skips(item, today):
                self._save(sid, item)
            if item.recurring or not item.enabled or item.on is None:
                continue
            when = datetime.combine(item.on, item.at, tzinfo=dt_util.get_default_time_zone())
            if when > now:
                continue
            if now - when <= timedelta(minutes=MISSED_GRACE_MINUTES):
                late.append(sid)
            else:
                _LOGGER.warning("Removing %s: it was missed while Home Assistant was off", item.title())
                self.remove(sid)
        self._schedule(now)
        for sid in late:
            self._trigger(sid, now)

    @callback
    def async_stop(self) -> None:
        """Stop timers and anything ringing."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        for ring in list(self.rings.values()):
            ring.cancel()

    @callback
    def async_entry_updated(self) -> None:
        """Subentries or options changed (from the UI or from us)."""
        self._load()
        new = set(self.items) - self._known
        self._known = set(self.items)
        for sid in new:
            async_dispatcher_send(self.hass, f"{SIGNAL_NEW_ITEM}_{self.entry.entry_id}", sid)
        self._schedule(dt_util.now())

    def _load(self) -> None:
        items: dict[str, Item] = {}
        for sid, sub in self.entry.subentries.items():
            if sub.subentry_type not in (KIND_ALARM, KIND_REMINDER):
                continue
            try:
                items[sid] = Item.from_data(sub.subentry_type, sub.data)
            except (KeyError, ValueError):
                _LOGGER.exception("Ignoring unreadable %s %s", sub.subentry_type, sub.title)
        self.items = items

    # --- storage ----------------------------------------------------------

    def add(self, item: Item) -> str:
        """Store a new item; returns its id."""
        sub = ConfigSubentry(
            data=MappingProxyType(item.to_data()),
            subentry_type=item.kind,
            title=item.title(),
            unique_id=None,
        )
        self.hass.config_entries.async_add_subentry(self.entry, sub)
        return sub.subentry_id

    def _save(self, sid: str, item: Item) -> None:
        sub = self.entry.subentries.get(sid)
        if sub is None:
            return
        self.hass.config_entries.async_update_subentry(
            self.entry, sub, data=item.to_data(), title=item.title()
        )

    def update(self, sid: str, item: Item) -> None:
        """Save changes to an item."""
        self._save(sid, item)

    def remove(self, sid: str) -> None:
        """Delete an item."""
        if sid in self.entry.subentries:
            self.hass.config_entries.async_remove_subentry(self.entry, sid)
        self.items.pop(sid, None)
        self.next_fires.pop(sid, None)

    # --- scheduling -------------------------------------------------------

    @callback
    def _schedule(self, now: datetime) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        tz = dt_util.get_default_time_zone()
        self.next_fires = {}
        for sid, item in self.items.items():
            if (when := item.next_fire(now, tz)) is not None:
                self.next_fires[sid] = when
        if self.next_fires:
            earliest = min(self.next_fires.values())
            self._unsub_timer = async_track_point_in_time(self.hass, self._on_timer, earliest)
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}")

    @callback
    def _on_timer(self, now: datetime) -> None:
        self._unsub_timer = None
        due = [sid for sid, when in self.next_fires.items() if when <= now + timedelta(seconds=1)]
        fire_time = max([now, *(self.next_fires[sid] for sid in due)])
        for sid in due:
            self._trigger(sid, fire_time)
        self._schedule(fire_time)

    @callback
    def _trigger(self, sid: str, now: datetime) -> None:
        item = self.items.get(sid)
        if item is None:
            return
        _LOGGER.debug("Going off: %s", item.title())
        if item.kind == KIND_ALARM:
            self._start_ring(sid, item)
        else:
            self.hass.async_create_background_task(
                self._deliver_reminder(sid, item), f"{item.title()} reminder"
            )
        if not item.recurring:
            self.remove(sid)
        elif prune_skips(item, now.date()):
            self._save(sid, item)

    def next_alarm(self, kind: str) -> tuple[str, datetime] | None:
        """The next item of a kind to go off."""
        found = [
            (when, sid)
            for sid, when in self.next_fires.items()
            if self.items[sid].kind == kind
        ]
        if not found:
            return None
        when, sid = min(found)
        return sid, when

    # --- places -----------------------------------------------------------

    def placement_for_device(self, device_id: str | None) -> Placement:
        """Where a voice device is: its satellite, speaker and room."""
        ent_reg = er.async_get(self.hass)
        satellite = media_player = None
        if device_id:
            for entry in er.async_entries_for_device(ent_reg, device_id):
                if entry.domain == "assist_satellite" and satellite is None:
                    satellite = entry.entity_id
                if entry.domain == "media_player" and media_player is None:
                    media_player = entry.entity_id
        if satellite is None and (default := self.opt(OPT_DEFAULT_SATELLITE, None)):
            return self.placement_for_satellite(default)
        area_id = self._area_of_device(device_id)
        return Placement(satellite, media_player, area_id, self._area_name(area_id))

    def placement_for_satellite(self, satellite: str | None) -> Placement:
        """Placement from a satellite entity."""
        if not satellite:
            return Placement(None, None, None, None)
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(satellite)
        media_player = None
        area_id = None
        if entry is not None:
            area_id = entry.area_id or self._area_of_device(entry.device_id)
            if entry.device_id:
                for other in er.async_entries_for_device(ent_reg, entry.device_id):
                    if other.domain == "media_player":
                        media_player = other.entity_id
                        break
        return Placement(satellite, media_player, area_id, self._area_name(area_id))

    def placement_for_area(self, name: str) -> Placement | None:
        """The voice device in a named room."""
        areas = ar.async_get(self.hass)
        wanted = {a.id for a in areas.async_get_areas_by_alias(name)}
        if (area := areas.async_get_area_by_name(name)) is not None:
            wanted.add(area.id)
        if not wanted:
            return None
        ent_reg = er.async_get(self.hass)
        for entry in ent_reg.entities.values():
            if entry.domain != "assist_satellite":
                continue
            area_id = entry.area_id or self._area_of_device(entry.device_id)
            if area_id in wanted:
                return self.placement_for_satellite(entry.entity_id)
        return None

    def _area_of_device(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        return device.area_id if device else None

    def _area_name(self, area_id: str | None) -> str | None:
        if not area_id:
            return None
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else None

    def lights_in_area(self, area_id: str | None) -> list[str]:
        """Every light in a room."""
        if not area_id:
            return []
        ent_reg = er.async_get(self.hass)
        found = []
        for entry in ent_reg.entities.values():
            if entry.domain != "light" or entry.disabled_by:
                continue
            if (entry.area_id or self._area_of_device(entry.device_id)) == area_id:
                found.append(entry.entity_id)
        return found

    def wake_lights(self, item: Item) -> list[str]:
        """Lights an alarm turns on."""
        if item.lights:
            return list(item.lights)
        if not item.wake:
            return []
        if configured := self.opt(OPT_WAKE_LIGHTS, []):
            return list(configured)
        return self.lights_in_area(self.placement_for_satellite(item.satellite).area_id)

    # --- ringing ----------------------------------------------------------

    def _start_ring(self, sid: str, item: Item, test_seconds: float | None = None) -> bool:
        key = item.satellite or item.media_player or "default"
        if (current := self.rings.get(key)) is not None and not current.done:
            _LOGGER.debug("%s is already ringing; keeping that ring", key)
            return False
        ring = Ring(self, sid, item, key, test_seconds)
        self.rings[key] = ring
        ring.start()
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}")
        return True

    async def async_test(self, sid: str, seconds: float = TEST_SECONDS) -> str:
        """Try an alarm or reminder now, as configured. Returns what happened."""
        item = self.items.get(sid)
        if item is None:
            raise HomeAssistantError("That alarm or reminder no longer exists")
        if not (item.satellite or item.media_player):
            raise HomeAssistantError("It has no voice device to play on. Edit it and pick one")
        if item.kind == KIND_REMINDER:
            await self.async_say(item, f"Reminder: {item.message}" if item.message else "Reminder")
            return "spoken"
        if not self._start_ring(sid, item, test_seconds=seconds):
            raise HomeAssistantError("That device is already ringing. Stop it first")
        return "ringing"

    async def async_test_sound(self, sound: str, satellite: str) -> None:
        """Play one sound once on a voice device."""
        placement = self.placement_for_satellite(satellite)
        item = Item(kind=KIND_ALARM, at=dt_util.now().time(), satellite=satellite, media_player=placement.media_player)
        await self.async_play(item, sounds.media_id(self.hass, sound))

    def _ring_finished(self, ring: Ring) -> None:
        if self.rings.get(ring.key) is ring:
            del self.rings[ring.key]
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}")

    @property
    def ringing(self) -> bool:
        """Whether any alarm is ringing."""
        return any(not ring.done for ring in self.rings.values())

    async def async_stop_ringing(self) -> int:
        """Stop every ringing alarm. Returns how many were stopped."""
        rings = [ring for ring in self.rings.values() if not ring.done]
        for ring in rings:
            await ring.async_stop()
        return len(rings)

    async def async_snooze(self, minutes: int | None = None) -> datetime | None:
        """Stop ringing alarms and ring them again later."""
        rings = [ring for ring in self.rings.values() if not ring.done]
        if not rings:
            return None
        minutes = minutes or int(self.opt(OPT_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES))
        tz = dt_util.get_default_time_zone()
        when = (dt_util.now() + timedelta(minutes=minutes)).astimezone(tz)
        when = when.replace(second=0, microsecond=0)
        for ring in rings:
            await ring.async_stop(snoozed=True)
            again = Item(
                kind=KIND_ALARM,
                at=when.time(),
                on=when.date(),
                label=ring.item.label,
                satellite=ring.item.satellite,
                media_player=ring.item.media_player,
                sound=ring.item.sound,
                snooze=True,
            )
            self.add(again)
        return when

    def sound_media_id(self, item: Item) -> str:
        """Media ID of an alarm's sound."""
        return sounds.media_id(
            self.hass, item.sound or self.opt(OPT_DEFAULT_SOUND, DEFAULT_SOUND)
        )

    async def _deliver_reminder(self, sid: str, item: Item) -> None:
        text = f"Reminder: {item.message}" if item.message else "Reminder"
        self.hass.bus.async_fire(
            EVENT_REMINDER, {"id": sid, "message": item.message, "satellite": item.satellite}
        )
        if item.notify_phone:
            for target in self.opt(OPT_NOTIFY_TARGETS, []):
                service = target.removeprefix("notify.")
                try:
                    await self.hass.services.async_call(
                        "notify",
                        service,
                        {"title": "Reminder", "message": item.message or "Reminder"},
                        blocking=True,
                    )
                except (HomeAssistantError, ValueError) as err:
                    _LOGGER.warning("Could not send the reminder to %s: %s", target, err)
        if item.satellite or item.media_player:
            for _attempt in range(3):
                try:
                    await self.async_say(item, text)
                    break
                except HomeAssistantError as err:
                    _LOGGER.debug("Reminder announcement failed (%s); retrying", err)
                    await asyncio.sleep(20)

    async def async_say(self, item: Item, message: str, preannounce: bool = True) -> None:
        """Speak a message on the item's device."""
        if item.satellite and self.hass.states.get(item.satellite):
            await self.hass.services.async_call(
                "assist_satellite",
                "announce",
                {"message": message, "preannounce": preannounce},
                target={ATTR_ENTITY_ID: item.satellite},
                blocking=True,
            )
        elif item.media_player:
            await self.hass.services.async_call(
                "tts",
                "speak",
                {"media_player_entity_id": item.media_player, "message": message},
                target={ATTR_ENTITY_ID: self._tts_entity()},
                blocking=True,
            )

    def _tts_entity(self) -> str | None:
        states = self.hass.states.async_all("tts")
        return states[0].entity_id if states else None

    async def async_play(self, item: Item, media: str) -> None:
        """Play a sound once on the item's device and wait for it to finish."""
        if item.satellite and self.hass.states.get(item.satellite):
            await self.hass.services.async_call(
                "assist_satellite",
                "announce",
                {"media_id": media, "preannounce": False},
                target={ATTR_ENTITY_ID: item.satellite},
                blocking=True,
            )
        elif item.media_player:
            await self.hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "media_content_id": media,
                    "media_content_type": "music",
                    "announce": True,
                },
                target={ATTR_ENTITY_ID: item.media_player},
                blocking=True,
            )
        else:
            raise HomeAssistantError("This alarm has no speaker")


class Ring:
    """One alarm going off on one device."""

    def __init__(
        self,
        manager: AlarmManager,
        sid: str,
        item: Item,
        key: str,
        test_seconds: float | None = None,
    ) -> None:
        """Initialise. A test rings for test_seconds at full volume, without lights."""
        self.test = test_seconds is not None
        self.test_seconds = test_seconds
        self.manager = manager
        self.hass = manager.hass
        self.sid = sid
        self.item = item
        self.key = key
        self.done = False
        self.started = dt_util.utcnow()
        self._task: asyncio.Task | None = None
        self._side_tasks: list[asyncio.Task] = []
        self._old_volume: float | None = None
        self._stopping = False

    def start(self) -> None:
        """Start ringing in the background."""
        self._task = self.hass.async_create_background_task(
            self._run(), f"alarm {self.item.title()}"
        )

    def cancel(self) -> None:
        """Cancel without cleanup (shutdown)."""
        for task in (self._task, *self._side_tasks):
            if task:
                task.cancel()
        self.done = True

    async def async_stop(self, snoozed: bool = False) -> None:
        """Stop ringing and put things back."""
        if self.done or self._stopping:
            return
        self._stopping = True
        for task in self._side_tasks:
            task.cancel()
        if self._task:
            self._task.cancel()
        player = self.item.media_player
        if player and self.hass.states.get(player):
            try:
                await self.hass.services.async_call(
                    "media_player", "media_stop", target={ATTR_ENTITY_ID: player}, blocking=True
                )
            except HomeAssistantError:
                pass
        await self._restore_volume()
        self._finish("snoozed" if snoozed else "stopped")

    def _finish(self, reason: str) -> None:
        if self.done:
            return
        self.done = True
        self.hass.bus.async_fire(
            EVENT_STOPPED,
            {"id": self.sid, "label": self.item.label, "satellite": self.item.satellite, "reason": reason},
        )
        self.manager._ring_finished(self)

    async def _run(self) -> None:
        manager = self.manager
        item = self.item
        lights = [] if self.test else manager.wake_lights(item)
        self.hass.bus.async_fire(
            EVENT_RINGING,
            {
                "id": self.sid,
                "label": item.label,
                "satellite": item.satellite,
                "media_player": item.media_player,
                "wake": item.wake,
                "lights": lights,
                "test": self.test,
            },
        )
        ring_seconds = (
            self.test_seconds
            if self.test_seconds is not None
            else 60 * float(manager.opt(OPT_RING_MINUTES, DEFAULT_RING_MINUTES))
        )
        target = float(manager.opt(OPT_VOLUME, DEFAULT_VOLUME)) / 100
        try:
            if item.media_player and (state := self.hass.states.get(item.media_player)):
                self._old_volume = state.attributes.get("volume_level")
                if item.wake and not self.test:
                    start = float(manager.opt(OPT_START_VOLUME, DEFAULT_START_VOLUME)) / 100
                    ramp = 60 * float(manager.opt(OPT_RAMP_MINUTES, DEFAULT_RAMP_MINUTES))
                    self._side(self._ramp_volume(min(start, target), target, ramp))
                else:
                    await self._set_volume(target)
            if lights:
                self._side(self._ramp_lights(lights))
            if item.label and not item.snooze:
                try:
                    await manager.async_say(item, item.label, preannounce=False)
                except HomeAssistantError as err:
                    _LOGGER.debug("Could not say the alarm label: %s", err)
            media = manager.sound_media_id(item)
            deadline = monotonic_time.monotonic() + ring_seconds
            while monotonic_time.monotonic() < deadline:
                began = monotonic_time.monotonic()
                try:
                    await manager.async_play(item, media)
                except HomeAssistantError as err:
                    _LOGGER.warning("Alarm sound failed on %s: %s", self.key, err)
                if monotonic_time.monotonic() - began < 1:
                    await asyncio.sleep(5)  # failed or returned at once; don't spin
                else:
                    await asyncio.sleep(2)  # a gap to say the wake word
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Alarm %s failed", item.title())
        if not self._stopping:
            for task in self._side_tasks:
                task.cancel()
            await self._restore_volume()
            self._finish("timeout")

    def _side(self, coro: Any) -> None:
        self._side_tasks.append(
            self.hass.async_create_background_task(coro, f"alarm {self.item.title()} ramp")
        )

    async def _set_volume(self, level: float) -> None:
        if not self.item.media_player:
            return
        try:
            await self.hass.services.async_call(
                "media_player",
                "volume_set",
                {"volume_level": round(max(0.0, min(1.0, level)), 2)},
                target={ATTR_ENTITY_ID: self.item.media_player},
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.debug("Could not set volume: %s", err)

    async def _ramp_volume(self, start: float, end: float, seconds: float) -> None:
        began = monotonic_time.monotonic()
        while True:
            progress = 1.0 if seconds <= 0 else (monotonic_time.monotonic() - began) / seconds
            await self._set_volume(start + (end - start) * min(1.0, progress))
            if progress >= 1:
                return
            await asyncio.sleep(VOLUME_STEP_SECONDS)

    async def _restore_volume(self) -> None:
        if self._old_volume is not None:
            await self._set_volume(float(self._old_volume))
            self._old_volume = None

    async def _ramp_lights(self, lights: list[str]) -> None:
        manager = self.manager
        brightness = float(manager.opt(OPT_BRIGHTNESS, DEFAULT_BRIGHTNESS))
        kelvin = int(manager.opt(OPT_KELVIN, DEFAULT_KELVIN))
        seconds = 60 * float(manager.opt(OPT_LIGHT_RAMP_MINUTES, DEFAULT_LIGHT_RAMP_MINUTES))
        if not self.item.wake:
            seconds = 0
        began = monotonic_time.monotonic()
        while True:
            progress = 1.0 if seconds <= 0 else (monotonic_time.monotonic() - began) / seconds
            level = max(1, round(brightness * min(1.0, progress)))
            try:
                await self.hass.services.async_call(
                    "light",
                    "turn_on",
                    {"brightness_pct": level, "color_temp_kelvin": kelvin},
                    target={ATTR_ENTITY_ID: lights},
                    blocking=True,
                    context=Context(),
                )
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("Could not set alarm lights: %s", err)
            if progress >= 1:
                return
            await asyncio.sleep(LIGHT_STEP_SECONDS)


def describe_place(placement: Placement) -> str:
    """'in the Bedroom' or 'on the Bedroom Dot'."""
    if placement.area_name:
        return f"in the {placement.area_name}"
    return ""


RemoveCallback = Callable[[], None]
