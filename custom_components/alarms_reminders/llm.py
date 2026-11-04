"""Tools that let a conversation agent (like Claude) manage alarms and reminders.

Home Assistant 2026.10 and later collect LLM tools from each integration's
`llm.py` platform.
"""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.llm import (
    LLM_API_ASSIST,
    LLMContext,
    Tool,
    ToolAnnotations,
    ToolInput,
)

from . import actions
from .compat import vol
from .const import DOMAIN, KIND_ALARM, KIND_REMINDER
from .manager import AlarmManager

PROMPT = """Alarms and reminders: always use the alarms_reminders tools for these, never a timer or a delayed command, even for "remind me in 15 minutes".
- Only make an alarm or reminder recurring when the user says so ("every weekday", "every Monday", "daily"). Otherwise it is one time.
- Pass times as 24-hour HH:MM and dates as YYYY-MM-DD, worked out from the current date and time. For "in 20 minutes" use in_minutes.
- If the user asks for a reminder without a time, ask them what time before calling the tool. Don't guess a time.
- After setting or changing one, repeat the tool's confirmation, including whether it is one time or recurring and the room.
- "Skip" means skip one day of a repeating alarm without deleting it."""

KIND = vol.In([KIND_ALARM, KIND_REMINDER, "all"])

_WRITE = ToolAnnotations(destructive=False, open_world=False)
_DELETE = ToolAnnotations(destructive=True, open_world=False)
_READ = ToolAnnotations(read_only=True, destructive=False, open_world=False)


def _manager(hass: HomeAssistant) -> AlarmManager | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


class _AlarmTool(Tool):
    integration = DOMAIN

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> dict[str, Any]:
        manager = _manager(hass)
        if manager is None:
            return {"success": False, "speech": "Alarms & Reminders isn't set up."}
        args = {k: v for k, v in tool_input.tool_args.items() if v not in (None, "")}
        return await self.run(manager, args, llm_context)

    async def run(
        self, manager: AlarmManager, args: dict[str, Any], llm_context: LLMContext
    ) -> dict[str, Any]:
        raise NotImplementedError


class SetAlarmTool(_AlarmTool):
    name = f"{DOMAIN}__set_alarm"
    title = "Set an alarm"
    description = (
        "Sets an alarm that rings with a sound. One time unless the user asks "
        "for a repeat. Rings in the room the user is speaking from unless "
        "they name another room."
    )
    annotations = _WRITE
    parameters = vol.Schema(
        {
            vol.Optional("time", description="24-hour HH:MM"): cv.string,
            vol.Optional(
                "date",
                description="YYYY-MM-DD for a one-time alarm on a later day. Leave out for the next time that clock time comes round.",
            ): cv.string,
            vol.Optional("in_minutes", description="For 'in N minutes' instead of time"): vol.Coerce(float),
            vol.Optional(
                "repeat",
                description="Only when asked: weekdays, weekends, daily, or day names like 'monday, wednesday'",
            ): cv.string,
            vol.Optional("label", description="Short name, if the user gave one"): cv.string,
            vol.Optional("room", description="Only when the user names a room"): cv.string,
            vol.Optional(
                "wake_up",
                description="True for wake-up alarms ('wake me up', morning alarms): lights and volume ease up",
            ): cv.boolean,
        }
    )

    async def run(self, manager, args, llm_context):
        return actions.set_item(
            manager,
            KIND_ALARM,
            time=args.get("time"),
            date=args.get("date"),
            in_minutes=args.get("in_minutes"),
            repeat=args.get("repeat"),
            label=args.get("label"),
            room=args.get("room"),
            wake_up=args.get("wake_up"),
            device_id=llm_context.device_id,
        )


class SetReminderTool(_AlarmTool):
    name = f"{DOMAIN}__set_reminder"
    title = "Set a reminder"
    description = (
        "Sets a reminder that is spoken in the room and sent to the user's "
        "phone. Needs a time: if the user gave only a day or nothing, ask "
        "for the time first. One time unless the user asks for a repeat."
    )
    annotations = _WRITE
    parameters = vol.Schema(
        {
            vol.Required("message", description="What to remind them about, e.g. 'call the vet'"): cv.string,
            vol.Optional("time", description="24-hour HH:MM"): cv.string,
            vol.Optional("date", description="YYYY-MM-DD for a later day"): cv.string,
            vol.Optional("in_minutes", description="For 'in N minutes' instead of time"): vol.Coerce(float),
            vol.Optional("repeat", description="Only when asked: weekdays, weekends, daily, or day names"): cv.string,
            vol.Optional("room", description="Only when the user names a room"): cv.string,
        }
    )

    async def run(self, manager, args, llm_context):
        return actions.set_item(
            manager,
            KIND_REMINDER,
            time=args.get("time"),
            date=args.get("date"),
            in_minutes=args.get("in_minutes"),
            repeat=args.get("repeat"),
            message=args.get("message"),
            room=args.get("room"),
            device_id=llm_context.device_id,
        )


class ListTool(_AlarmTool):
    name = f"{DOMAIN}__list"
    title = "List alarms and reminders"
    description = "Lists the user's alarms and/or reminders with when they go off next."
    annotations = _READ
    parameters = vol.Schema({vol.Optional("kind"): KIND})

    async def run(self, manager, args, llm_context):
        return actions.list_items(manager, args.get("kind"))


_TARGET = vol.Optional(
    "target",
    description="Which one: its time ('7:00'), its label or reminder text, 'next', or 'all'",
)


class ChangeTool(_AlarmTool):
    name = f"{DOMAIN}__change"
    title = "Change an alarm or reminder"
    description = (
        "Changes an existing alarm or reminder: time, date, repeat days, "
        "label/message, room, wake-up, or turns it on or off."
    )
    annotations = _WRITE
    parameters = vol.Schema(
        {
            vol.Optional("kind"): KIND,
            _TARGET: cv.string,
            vol.Optional("time", description="New 24-hour HH:MM"): cv.string,
            vol.Optional("date", description="New YYYY-MM-DD (makes it one time)"): cv.string,
            vol.Optional("repeat", description="New repeat days, or 'once' to stop repeating"): cv.string,
            vol.Optional("label"): cv.string,
            vol.Optional("message"): cv.string,
            vol.Optional("room"): cv.string,
            vol.Optional("wake_up"): cv.boolean,
            vol.Optional("enabled", description="False turns it off without deleting it"): cv.boolean,
        }
    )

    async def run(self, manager, args, llm_context):
        return actions.change(
            manager,
            args.get("kind"),
            args.get("target"),
            time=args.get("time"),
            date=args.get("date"),
            repeat=args.get("repeat"),
            label=args.get("label"),
            message=args.get("message"),
            room=args.get("room"),
            wake_up=args.get("wake_up"),
            enabled=args.get("enabled"),
        )


class CancelTool(_AlarmTool):
    name = f"{DOMAIN}__cancel"
    title = "Delete an alarm or reminder"
    description = "Deletes alarms or reminders. For one day of a repeating alarm use skip instead."
    annotations = _DELETE
    parameters = vol.Schema({vol.Optional("kind"): KIND, _TARGET: cv.string})

    async def run(self, manager, args, llm_context):
        return actions.cancel(manager, args.get("kind"), args.get("target"))


class SkipTool(_AlarmTool):
    name = f"{DOMAIN}__skip"
    title = "Skip a repeating alarm once"
    description = (
        "Skips one day of a repeating alarm ('skip tomorrow's alarm') without "
        "deleting it. Leave date out to skip the next one."
    )
    annotations = _WRITE
    parameters = vol.Schema(
        {
            _TARGET: cv.string,
            vol.Optional("date", description="YYYY-MM-DD of the day to skip"): cv.string,
        }
    )

    async def run(self, manager, args, llm_context):
        return actions.skip(manager, KIND_ALARM, args.get("target"), args.get("date"))


class StopTool(_AlarmTool):
    name = f"{DOMAIN}__stop"
    title = "Stop a ringing alarm"
    description = "Stops an alarm that is ringing right now."
    annotations = _WRITE

    async def run(self, manager, args, llm_context):
        return await actions.stop(manager)


class SnoozeTool(_AlarmTool):
    name = f"{DOMAIN}__snooze"
    title = "Snooze a ringing alarm"
    description = "Snoozes an alarm that is ringing right now."
    annotations = _WRITE
    parameters = vol.Schema({vol.Optional("minutes"): vol.Coerce(float)})

    async def run(self, manager, args, llm_context):
        return await actions.snooze(manager, args.get("minutes"))


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return the alarm tools for the Assist API."""
    if api_id != LLM_API_ASSIST or _manager(hass) is None:
        return None
    tools: list[Tool] = [
        SetAlarmTool(),
        SetReminderTool(),
        ListTool(),
        ChangeTool(),
        CancelTool(),
        SkipTool(),
        StopTool(),
        SnoozeTool(),
    ]
    return LLMTools(tools=tools, prompt=PROMPT)
