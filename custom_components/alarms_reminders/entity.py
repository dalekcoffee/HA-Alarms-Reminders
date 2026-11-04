"""Shared entity bits."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATED, TITLE
from .manager import AlarmManager


class AlarmsEntity(Entity):
    """Updates whenever alarms change or ring."""

    _attr_should_poll = False

    def __init__(self, manager: AlarmManager) -> None:
        """Initialise."""
        self.manager = manager

    async def async_added_to_hass(self) -> None:
        """Listen for changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATED}_{self.manager.entry.entry_id}",
                self._changed,
            )
        )

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class HubEntity(AlarmsEntity):
    """An entity on the integration's own device."""

    _attr_has_entity_name = True

    def __init__(self, manager: AlarmManager, key: str) -> None:
        """Initialise."""
        super().__init__(manager)
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name=TITLE,
            entry_type=DeviceEntryType.SERVICE,
        )
