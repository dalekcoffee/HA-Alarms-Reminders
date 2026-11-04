"""Stop and snooze buttons, and a Test button for each alarm and reminder."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KIND_ALARM, KIND_REMINDER, SIGNAL_NEW_ITEM
from .entity import AlarmsEntity, HubEntity
from .manager import AlarmManager


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the buttons."""
    manager: AlarmManager = entry.runtime_data
    async_add_entities([StopButton(manager, "stop"), SnoozeButton(manager, "snooze")])
    for sid, sub in entry.subentries.items():
        if sub.subentry_type in (KIND_ALARM, KIND_REMINDER):
            async_add_entities([TestButton(manager, sid)], config_subentry_id=sid)

    @callback
    def _new(sid: str) -> None:
        async_add_entities([TestButton(manager, sid)], config_subentry_id=sid)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_ITEM}_{entry.entry_id}", _new)
    )


class StopButton(HubEntity, ButtonEntity):
    """Stop ringing alarms."""

    async def async_press(self) -> None:
        """Stop."""
        await self.manager.async_stop_ringing()


class SnoozeButton(HubEntity, ButtonEntity):
    """Snooze ringing alarms."""

    async def async_press(self) -> None:
        """Snooze."""
        await self.manager.async_snooze()


class TestButton(AlarmsEntity, ButtonEntity):
    """Ring an alarm (30 seconds) or speak a reminder now, as configured."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, manager: AlarmManager, sid: str) -> None:
        """Initialise."""
        super().__init__(manager)
        self.sid = sid
        self._attr_unique_id = f"{sid}_test"

    @property
    def available(self) -> bool:
        """Gone once deleted."""
        return self.sid in self.manager.items

    @property
    def name(self) -> str:
        """'Test Alarm 7:00 AM, weekdays'."""
        item = self.manager.items.get(self.sid)
        sub = self.manager.entry.subentries.get(self.sid)
        return "Test " + (item.title() if item else (sub.title if sub else "alarm"))

    async def async_press(self) -> None:
        """Ring it now."""
        await self.manager.async_test(self.sid)
