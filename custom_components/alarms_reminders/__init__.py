"""Alarms & Reminders: voice-set alarms and reminders for Assist satellites."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from . import actions, sounds
from .compat import vol
from .const import DOMAIN, KIND_ALARM, KIND_REMINDER
from .manager import AlarmManager
from .voice import async_setup_voice

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATE,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AlarmsConfigEntry = ConfigEntry[AlarmManager]


def _manager(hass: HomeAssistant) -> AlarmManager:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError("Alarms & Reminders isn't loaded")


def _sid_for_entity(hass: HomeAssistant, entity_id: str) -> str:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != DOMAIN or not entry.config_subentry_id:
        raise ServiceValidationError(f"{entity_id} isn't an alarm or reminder")
    return entry.config_subentry_id


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services."""

    def _result(result: dict) -> ServiceResponse:
        if not result.get("success"):
            raise ServiceValidationError(result["speech"])
        return result

    async def set_alarm(call: ServiceCall) -> ServiceResponse:
        return _result(
            actions.set_item(
                _manager(hass),
                KIND_ALARM,
                time=call.data.get("time"),
                date=call.data.get("date"),
                repeat=call.data.get("repeat"),
                label=call.data.get("label"),
                room=call.data.get("room"),
                wake_up=call.data.get("wake_up"),
            )
        )

    async def set_reminder(call: ServiceCall) -> ServiceResponse:
        return _result(
            actions.set_item(
                _manager(hass),
                KIND_REMINDER,
                time=call.data.get("time"),
                date=call.data.get("date"),
                repeat=call.data.get("repeat"),
                message=call.data.get("message"),
                room=call.data.get("room"),
            )
        )

    async def skip_next(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        results = [
            _result(actions.skip(manager, None, _sid_for_entity(hass, eid), call.data.get("date")))
            for eid in call.data["entity_id"]
        ]
        return {"results": results}

    async def delete(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        for eid in call.data["entity_id"]:
            manager.remove(_sid_for_entity(hass, eid))
        return None

    async def list_items(call: ServiceCall) -> ServiceResponse:
        return actions.list_items(_manager(hass), call.data.get("kind"))

    async def test(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        for eid in call.data["entity_id"]:
            try:
                await manager.async_test(_sid_for_entity(hass, eid), call.data.get("seconds", 30))
            except HomeAssistantError as err:
                raise ServiceValidationError(str(err)) from err
        return None

    async def stop(call: ServiceCall) -> ServiceResponse:
        await actions.stop(_manager(hass))
        return None

    async def vacation_on(call: ServiceCall) -> ServiceResponse:
        result = actions.vacation_on(_manager(hass), call.data["end_date"])
        if not result["success"]:
            raise ServiceValidationError(result["speech"])
        return result

    async def vacation_off(call: ServiceCall) -> ServiceResponse:
        return actions.vacation_off(_manager(hass))

    async def snooze(call: ServiceCall) -> ServiceResponse:
        await actions.snooze(_manager(hass), call.data.get("minutes"))
        return None

    when = {
        vol.Required("time"): cv.string,
        vol.Optional("date"): cv.string,
        vol.Optional("repeat"): cv.string,
        vol.Optional("room"): cv.string,
    }
    hass.services.async_register(
        DOMAIN, "set_alarm", set_alarm,
        vol.Schema({**when, vol.Optional("label"): cv.string, vol.Optional("wake_up"): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "set_reminder", set_reminder,
        vol.Schema({**when, vol.Required("message"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "skip_next", skip_next,
        vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Optional("date"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "delete", delete, vol.Schema({vol.Required("entity_id"): cv.entity_ids})
    )
    hass.services.async_register(
        DOMAIN, "list", list_items,
        vol.Schema({vol.Optional("kind"): vol.In([KIND_ALARM, KIND_REMINDER, "all"])}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "test", test,
        vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Optional("seconds", default=30): vol.All(vol.Coerce(int), vol.Range(min=5, max=600))}),
    )
    hass.services.async_register(DOMAIN, "stop", stop)
    hass.services.async_register(
        DOMAIN, "vacation_on", vacation_on,
        vol.Schema({vol.Required("end_date"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "vacation_off", vacation_off, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "snooze", snooze, vol.Schema({vol.Optional("minutes"): vol.Coerce(int)})
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlarmsConfigEntry) -> bool:
    """Set up from a config entry."""
    await sounds.async_ensure_built_ins(hass)
    manager = AlarmManager(hass, entry)
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_updated))
    entry.async_on_unload(async_setup_voice(hass, manager))
    entry.async_on_unload(manager.async_stop)
    return True


async def _async_updated(hass: HomeAssistant, entry: AlarmsConfigEntry) -> None:
    # Subentry and option changes are applied live, so a ringing alarm
    # isn't interrupted by a reload.
    entry.runtime_data.async_entry_updated()


async def async_unload_entry(hass: HomeAssistant, entry: AlarmsConfigEntry) -> bool:
    """Unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
