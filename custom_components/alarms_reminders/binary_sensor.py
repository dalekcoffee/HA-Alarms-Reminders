"""'Alarm ringing' binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the ringing sensor."""
    async_add_entities([RingingSensor(entry.runtime_data, "ringing")])


class RingingSensor(HubEntity, BinarySensorEntity):
    """On while any alarm is ringing."""

    @property
    def is_on(self) -> bool:
        """Ringing?"""
        return self.manager.ringing

    @property
    def extra_state_attributes(self) -> dict:
        """What's ringing."""
        return {
            "ringing": [
                {"id": ring.sid, "label": ring.item.label, "satellite": ring.item.satellite}
                for ring in self.manager.rings.values()
                if not ring.done
            ]
        }
