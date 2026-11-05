"""'Next alarm' and 'Next reminder' sensors, with the full list for dashboards."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import KIND_ALARM, KIND_REMINDER
from .entity import HubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the sensors."""
    manager = entry.runtime_data
    async_add_entities(
        [NextSensor(manager, KIND_ALARM), NextSensor(manager, KIND_REMINDER)]
    )


class NextSensor(HubEntity, SensorEntity):
    """When the next alarm or reminder goes off."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager, kind: str) -> None:
        """Initialise."""
        super().__init__(manager, f"next_{kind}")
        self.kind = kind

    @property
    def native_value(self) -> datetime | None:
        """Next time."""
        found = self.manager.next_alarm(self.kind)
        return found[1] if found else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Every item of this kind, soonest first."""
        now = dt_util.now()
        tz = dt_util.get_default_time_zone()
        rows = []
        for sid, item in self.manager.items.items():
            if item.kind != self.kind:
                continue
            nxt = self.manager.next_fires.get(sid)
            rows.append(
                {
                    "id": sid,
                    "title": item.title(),
                    "when": item.describe(now, tz),
                    "recurring": item.recurring,
                    "enabled": item.enabled,
                    "next": nxt.isoformat() if nxt else None,
                    "skipped": [d.isoformat() for d in sorted(item.skip)],
                    "room": self.manager.placement_for_satellite(item.satellite).area_name,
                }
            )
        rows.sort(key=lambda r: (r["next"] is None, r["next"] or ""))
        attrs: dict[str, Any] = {"items": rows, "count": len(rows)}
        if self.kind == KIND_ALARM:
            end = self.manager.vacation_end
            attrs["vacation_until"] = end.isoformat() if end else None
        return attrs
