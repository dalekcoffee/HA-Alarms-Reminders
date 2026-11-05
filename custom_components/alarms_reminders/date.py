"""The last day of vacation mode."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the vacation end date."""
    async_add_entities([VacationEndDate(entry.runtime_data, "vacation_end")])


class VacationEndDate(HubEntity, DateEntity):
    """Alarms stay off until midnight at the end of this day."""

    _attr_icon = "mdi:calendar-end"

    @property
    def native_value(self) -> date | None:
        """The date that's set."""
        return self.manager.vacation_end_date

    async def async_set_value(self, value: date) -> None:
        """Change the date. Turning vacation mode on is the switch's job."""
        if value < dt_util.now().date():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="vacation_end_passed"
            )
        self.manager.set_vacation(self.manager.vacation_end is not None, value)
