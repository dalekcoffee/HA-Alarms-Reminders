"""Stop and snooze buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the buttons."""
    manager = entry.runtime_data
    async_add_entities([StopButton(manager, "stop"), SnoozeButton(manager, "snooze")])


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
