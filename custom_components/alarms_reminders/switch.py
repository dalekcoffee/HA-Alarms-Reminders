"""One switch per alarm or reminder: turn it on or off without deleting it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import KIND_ALARM, SIGNAL_NEW_ITEM
from .entity import AlarmsEntity
from .manager import AlarmManager


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add a switch for each item, now and whenever one is added."""
    manager: AlarmManager = entry.runtime_data
    for sid, sub in entry.subentries.items():
        if sid in manager.items or sub.subentry_type in ("alarm", "reminder"):
            async_add_entities([ItemSwitch(manager, sid)], config_subentry_id=sid)

    @callback
    def _new(sid: str) -> None:
        async_add_entities([ItemSwitch(manager, sid)], config_subentry_id=sid)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_NEW_ITEM}_{entry.entry_id}", _new)
    )


class ItemSwitch(AlarmsEntity, SwitchEntity):
    """On = the alarm or reminder is active."""

    def __init__(self, manager: AlarmManager, sid: str) -> None:
        """Initialise."""
        super().__init__(manager)
        self.sid = sid
        self._attr_unique_id = sid

    @property
    def available(self) -> bool:
        """Gone once deleted."""
        return self.sid in self.manager.items

    @property
    def name(self) -> str:
        """Title, kept in step with edits."""
        item = self.manager.items.get(self.sid)
        sub = self.manager.entry.subentries.get(self.sid)
        return item.title() if item else (sub.title if sub else "Alarm")

    @property
    def icon(self) -> str:
        """Bell or note."""
        item = self.manager.items.get(self.sid)
        if item is None or item.kind == KIND_ALARM:
            return "mdi:alarm" if self.is_on else "mdi:alarm-off"
        return "mdi:bell-ring-outline" if self.is_on else "mdi:bell-off-outline"

    @property
    def is_on(self) -> bool:
        """Enabled?"""
        item = self.manager.items.get(self.sid)
        return bool(item and item.enabled)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details for dashboards."""
        item = self.manager.items.get(self.sid)
        if item is None:
            return {}
        nxt = self.manager.next_fires.get(self.sid)
        return {
            "kind": item.kind,
            "when": item.describe(dt_util.now(), dt_util.get_default_time_zone()),
            "recurring": item.recurring,
            "next": nxt.isoformat() if nxt else None,
            "skipped": [d.isoformat() for d in sorted(item.skip)],
            "label": item.label or item.message,
            "room": self.manager.placement_for_satellite(item.satellite).area_name,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable."""
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable."""
        self._set(False)

    def _set(self, enabled: bool) -> None:
        item = self.manager.items.get(self.sid)
        if item is None:
            return
        item.enabled = enabled
        self.manager.update(self.sid, item)
