"""Setup, settings, sounds, and the add/edit forms for alarms and reminders."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from . import sounds
from .compat import vol
from .const import (
    CONF_DATE,
    CONF_ENABLED,
    CONF_LABEL,
    CONF_LIGHTS,
    CONF_MESSAGE,
    CONF_NOTIFY,
    CONF_REPEAT,
    CONF_SATELLITE,
    CONF_SOUND,
    CONF_TIME,
    CONF_WAKE,
    DEFAULT_BRIGHTNESS,
    DEFAULT_KELVIN,
    DEFAULT_LIGHT_RAMP_MINUTES,
    DEFAULT_RAMP_MINUTES,
    DEFAULT_RING_MINUTES,
    DEFAULT_SNOOZE_MINUTES,
    DEFAULT_SOUND,
    DEFAULT_START_VOLUME,
    DEFAULT_VOLUME,
    DOMAIN,
    KIND_ALARM,
    KIND_REMINDER,
    OPT_BRIGHTNESS,
    OPT_DEFAULT_SATELLITE,
    OPT_DEFAULT_SOUND,
    OPT_KELVIN,
    OPT_LIGHT_RAMP_MINUTES,
    OPT_NOTIFY_ALARMS,
    OPT_NOTIFY_TARGETS,
    OPT_RAMP_MINUTES,
    OPT_RING_MINUTES,
    OPT_SNOOZE_MINUTES,
    OPT_SOUNDS,
    OPT_START_VOLUME,
    OPT_VOLUME,
    OPT_WAKE_LIGHTS,
    TITLE,
)
from .model import DAY_NAMES, Item, ParseError, first_date_for, parse_date, parse_time

SKIP_NEXT = "skip_next"
CLEAR_SKIPS = "clear_skips"


def _sound_selector(entry: ConfigEntry) -> selector.SelectSelector:
    names = sounds.sound_names(dict(entry.options.get(OPT_SOUNDS, {})))
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=k, label=v) for k, v in names.items()],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _minutes(low: int, high: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=low, max=high, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
    )


def _percent() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)
    )


_REPEAT = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=str(i), label=name.capitalize())
            for i, name in enumerate(DAY_NAMES)
        ],
        multiple=True,
        mode=selector.SelectSelectorMode.LIST,
    )
)


def _notify_targets(hass: HomeAssistant) -> list[str]:
    return sorted(
        f"notify.{name}"
        for name in hass.services.async_services_for_domain("notify")
        if name.startswith("mobile_app_")
    )


class AlarmsRemindersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the integration (one per Home Assistant)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry straight away; everything else is in Configure."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        return self.async_create_entry(title=TITLE, data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Settings and sounds."""
        return AlarmsRemindersOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """Alarms and reminders are subentries, so they can be added and edited in the UI."""
        return {KIND_ALARM: AlarmSubentryFlow, KIND_REMINDER: ReminderSubentryFlow}


class AlarmsRemindersOptionsFlow(OptionsFlow):
    """Settings, and adding or removing sounds."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Menu."""
        menu = ["settings", "test_sound", "add_sound_file", "add_sound_link"]
        if self.config_entry.options.get(OPT_SOUNDS):
            menu.append("remove_sound")
        return self.async_show_menu(step_id="init", menu_options=menu)

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Defaults for ringing, lights and phone notifications."""
        options = self.config_entry.options
        if user_input is not None:
            return self._save(**user_input)
        targets = sorted(set(_notify_targets(self.hass)) | set(options.get(OPT_NOTIFY_TARGETS, [])))
        schema = vol.Schema(
            {
                vol.Required(OPT_DEFAULT_SOUND, default=options.get(OPT_DEFAULT_SOUND, DEFAULT_SOUND)): _sound_selector(self.config_entry),
                vol.Optional(OPT_DEFAULT_SATELLITE, description={"suggested_value": options.get(OPT_DEFAULT_SATELLITE)}): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
                vol.Required(OPT_RING_MINUTES, default=options.get(OPT_RING_MINUTES, DEFAULT_RING_MINUTES)): _minutes(1, 60),
                vol.Required(OPT_SNOOZE_MINUTES, default=options.get(OPT_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES)): _minutes(1, 60),
                vol.Required(OPT_VOLUME, default=options.get(OPT_VOLUME, DEFAULT_VOLUME)): _percent(),
                vol.Required(OPT_START_VOLUME, default=options.get(OPT_START_VOLUME, DEFAULT_START_VOLUME)): _percent(),
                vol.Required(OPT_RAMP_MINUTES, default=options.get(OPT_RAMP_MINUTES, DEFAULT_RAMP_MINUTES)): _minutes(0, 60),
                vol.Optional(OPT_WAKE_LIGHTS, description={"suggested_value": options.get(OPT_WAKE_LIGHTS, [])}): selector.EntitySelector(selector.EntitySelectorConfig(domain="light", multiple=True)),
                vol.Required(OPT_LIGHT_RAMP_MINUTES, default=options.get(OPT_LIGHT_RAMP_MINUTES, DEFAULT_LIGHT_RAMP_MINUTES)): _minutes(0, 60),
                vol.Required(OPT_BRIGHTNESS, default=options.get(OPT_BRIGHTNESS, DEFAULT_BRIGHTNESS)): _percent(),
                vol.Required(OPT_KELVIN, default=options.get(OPT_KELVIN, DEFAULT_KELVIN)): selector.ColorTempSelector(selector.ColorTempSelectorConfig(unit="kelvin", min=2000, max=6500)),
                vol.Required(OPT_NOTIFY_ALARMS, default=options.get(OPT_NOTIFY_ALARMS, True)): selector.BooleanSelector(),
                vol.Optional(OPT_NOTIFY_TARGETS, description={"suggested_value": options.get(OPT_NOTIFY_TARGETS, [])}): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=targets, multiple=True, custom_value=True)
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_test_sound(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Play a sound once on a voice device. Stays open to try another."""
        errors: dict[str, str] = {}
        options = self.config_entry.options
        played = ""
        if user_input is not None:
            if self.config_entry.state is not ConfigEntryState.LOADED:
                errors["base"] = "not_loaded"
            else:
                try:
                    await self.config_entry.runtime_data.async_test_sound(
                        user_input["sound"], user_input["satellite"]
                    )
                except HomeAssistantError:
                    errors["base"] = "play_failed"
                else:
                    names = sounds.sound_names(dict(options.get(OPT_SOUNDS, {})))
                    played = names.get(user_input["sound"], user_input["sound"])
        last = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("sound", default=last.get("sound", options.get(OPT_DEFAULT_SOUND, DEFAULT_SOUND))): _sound_selector(self.config_entry),
                vol.Required("satellite", description={"suggested_value": last.get("satellite", options.get(OPT_DEFAULT_SATELLITE))}): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
            }
        )
        return self.async_show_form(
            step_id="test_sound",
            data_schema=schema,
            errors=errors,
            description_placeholders={"played": f"Played {played}. " if played else ""},
            last_step=False,
        )

    async def async_step_add_sound_file(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Upload an audio file."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                file_name = await self.hass.async_add_executor_job(
                    sounds.store_upload, self.hass, user_input["file"], user_input["name"]
                )
            except sounds.SoundError as err:
                errors["base"] = str(err)
            else:
                return self._add_sound(file_name, user_input["name"])
        schema = vol.Schema(
            {
                vol.Required("name"): selector.TextSelector(),
                vol.Required("file"): selector.FileSelector(selector.FileSelectorConfig(accept="audio/*,.mp3,.wav,.ogg,.flac,.m4a")),
            }
        )
        return self.async_show_form(step_id="add_sound_file", data_schema=schema, errors=errors)

    async def async_step_add_sound_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Download an audio file from a direct link."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                file_name = await sounds.async_download(self.hass, user_input["url"].strip(), user_input["name"])
            except sounds.SoundError as err:
                errors["base"] = str(err)
            else:
                return self._add_sound(file_name, user_input["name"])
        schema = vol.Schema(
            {
                vol.Required("name"): selector.TextSelector(),
                vol.Required("url"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            }
        )
        return self.async_show_form(step_id="add_sound_link", data_schema=schema, errors=errors)

    def _add_sound(self, file_name: str, name: str) -> ConfigFlowResult:
        library = dict(self.config_entry.options.get(OPT_SOUNDS, {}))
        library[file_name] = name.strip() or file_name
        return self._save(**{OPT_SOUNDS: library})

    async def async_step_remove_sound(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Remove one of your own sounds."""
        library = dict(self.config_entry.options.get(OPT_SOUNDS, {}))
        if user_input is not None:
            file_name = user_input["sound"]
            library.pop(file_name, None)
            try:
                await self.hass.async_add_executor_job(sounds.delete_sound, self.hass, file_name)
            except sounds.SoundError:
                pass
            changes: dict[str, Any] = {OPT_SOUNDS: library}
            if self.config_entry.options.get(OPT_DEFAULT_SOUND) == file_name:
                changes[OPT_DEFAULT_SOUND] = DEFAULT_SOUND
            return self._save(**changes)
        schema = vol.Schema(
            {
                vol.Required("sound"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=k, label=v) for k, v in library.items()]
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_sound", data_schema=schema)


class _ItemFlow(ConfigSubentryFlow):
    """Shared add/edit form."""

    kind: str

    def _entry_loaded(self) -> bool:
        return self._get_entry().state is ConfigEntryState.LOADED

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add one."""
        if not self._entry_loaded():
            return self.async_abort(reason="entry_not_loaded")
        return await self._form("user", None, user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit one."""
        if not self._entry_loaded():
            return self.async_abort(reason="entry_not_loaded")
        sub = self._get_reconfigure_subentry()
        return await self._form("reconfigure", Item.from_data(self.kind, sub.data), user_input)

    def _fields(self, item: Item | None) -> dict[Any, Any]:
        raise NotImplementedError

    def _apply(self, item: Item, user_input: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _form(self, step_id: str, current: Item | None, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        manager = self._get_entry().runtime_data
        if user_input is not None:
            tz = dt_util.get_default_time_zone()
            now = dt_util.now()
            try:
                at = parse_time(user_input[CONF_TIME])
                on = parse_date(user_input[CONF_DATE]) if user_input.get(CONF_DATE) else None
            except ParseError:
                errors["base"] = "bad_time"
            else:
                days = sorted(int(d) for d in user_input.get(CONF_REPEAT, []))
                item = current or Item(kind=self.kind, at=at)
                item.at = at
                item.repeat = days
                item.on = None if days else (on or first_date_for(at, now, tz))
                satellite = user_input.get(CONF_SATELLITE) or None
                placement = manager.placement_for_satellite(satellite)
                item.satellite = satellite
                item.media_player = placement.media_player
                item.enabled = user_input.get(CONF_ENABLED, True)
                self._apply(item, user_input)
                if user_input.get(CLEAR_SKIPS):
                    item.skip = []
                if user_input.get(SKIP_NEXT) and days and (nxt := item.next_fire(now, tz)):
                    item.skip.append(nxt.date())
                if not days and item.on and item.next_fire(now, tz) is None and item.enabled:
                    errors["base"] = "in_past"
                else:
                    if step_id == "user":
                        return self.async_create_entry(title=item.title(), data=item.to_data())
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=item.title(),
                        data=item.to_data(),
                    )

        item = current
        fields: dict[Any, Any] = {
            vol.Required(CONF_TIME, description={"suggested_value": item.at.strftime("%H:%M:%S") if item else None}): selector.TimeSelector(),
            vol.Optional(CONF_REPEAT, description={"suggested_value": [str(d) for d in item.repeat] if item else []}): _REPEAT,
            vol.Optional(CONF_DATE, description={"suggested_value": item.on.isoformat() if item and item.on else None}): selector.DateSelector(),
            **self._fields(item),
            vol.Optional(CONF_SATELLITE, description={"suggested_value": item.satellite if item else None}): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
            vol.Required(CONF_ENABLED, default=item.enabled if item else True): selector.BooleanSelector(),
        }
        if item and item.recurring:
            fields[vol.Optional(SKIP_NEXT, default=False)] = selector.BooleanSelector()
        if item and item.skip:
            fields[vol.Optional(CLEAR_SKIPS, default=False)] = selector.BooleanSelector()
        skipped = ", ".join(d.isoformat() for d in sorted(item.skip)) if item and item.skip else "none"
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"skipped": skipped},
        )


class AlarmSubentryFlow(_ItemFlow):
    """Add or edit an alarm."""

    kind = KIND_ALARM

    def _fields(self, item: Item | None) -> dict[Any, Any]:
        return {
            vol.Optional(CONF_LABEL, description={"suggested_value": item.label if item else None}): selector.TextSelector(),
            vol.Optional(CONF_SOUND, description={"suggested_value": item.sound if item else None}): _sound_selector(self._get_entry()),
            vol.Required(CONF_WAKE, default=item.wake if item else True): selector.BooleanSelector(),
            vol.Optional(CONF_LIGHTS, description={"suggested_value": item.lights if item else []}): selector.EntitySelector(selector.EntitySelectorConfig(domain="light", multiple=True)),
        }

    def _apply(self, item: Item, user_input: dict[str, Any]) -> None:
        item.label = (user_input.get(CONF_LABEL) or "").strip()
        item.sound = user_input.get(CONF_SOUND) or None
        item.wake = bool(user_input.get(CONF_WAKE, False))
        item.lights = list(user_input.get(CONF_LIGHTS) or [])
        item.snooze = False


class ReminderSubentryFlow(_ItemFlow):
    """Add or edit a reminder."""

    kind = KIND_REMINDER

    def _fields(self, item: Item | None) -> dict[Any, Any]:
        return {
            vol.Required(CONF_MESSAGE, description={"suggested_value": item.message if item else None}): selector.TextSelector(),
            vol.Required(CONF_NOTIFY, default=item.notify_phone if item else True): selector.BooleanSelector(),
        }

    def _apply(self, item: Item, user_input: dict[str, Any]) -> None:
        item.message = user_input[CONF_MESSAGE].strip()
        item.notify_phone = bool(user_input.get(CONF_NOTIFY, True))
