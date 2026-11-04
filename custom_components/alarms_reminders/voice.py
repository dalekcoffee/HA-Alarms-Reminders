"""Local sentences for stopping and snoozing, handled without the LLM.

"Stop the alarm" and "snooze the alarm" always work. While an alarm is
ringing, a bare "stop" or "snooze" works too. These need "Prefer handling
commands locally" on in the voice pipeline; otherwise the LLM's stop and
snooze tools handle them.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import actions
from .const import SIGNAL_UPDATED
from .manager import AlarmManager

_LOGGER = logging.getLogger(__name__)

STOP_SENTENCES = [
    "(stop|cancel|dismiss|turn off|shut off|silence) [the] alarm[s]",
    "alarm (off|stop)",
]
SNOOZE_SENTENCES = ["snooze [the] alarm[s]", "snooze [the] alarm[s] for {minutes} minute[s]"]
RINGING_STOP = ["stop", "stop it", "shut up", "be quiet", "enough", "i'm up", "i am up", "okay i'm up"]
RINGING_SNOOZE = ["snooze", "snooze it", "five more minutes", "snooze for {minutes} minute[s]"]


def _register(hass: HomeAssistant, sentences: list[str], handler: Any) -> CALLBACK_TYPE:
    # Not a public API, but it's how conversation triggers register sentences.
    from homeassistant.components.conversation.agent_manager import get_agent_manager

    manager = get_agent_manager(hass)
    try:
        from homeassistant.components.conversation.trigger import TriggerDetails
    except ImportError:  # Home Assistant 2026.10 and later
        return manager.register_trigger(sentences, handler)
    return manager.register_trigger(TriggerDetails(sentences, handler))


def _minutes(result: Any) -> Any:
    entity = result.entities.get("minutes") if result is not None else None
    if entity is None:
        return None
    return entity.value if entity.value not in (None, "") else entity.text


def _stop_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        return (await actions.stop(manager))["speech"]

    return on_sentence


def _snooze_handler(manager: AlarmManager):
    async def on_sentence(user_input: Any, result: Any) -> str:
        return (await actions.snooze(manager, _minutes(result)))["speech"]

    return on_sentence


def async_setup_voice(hass: HomeAssistant, manager: AlarmManager) -> CALLBACK_TYPE:
    """Register sentences. Returns a callback that removes them."""
    removers: list[CALLBACK_TYPE] = []
    ringing: list[CALLBACK_TYPE] = []
    try:
        removers.append(_register(hass, STOP_SENTENCES, _stop_handler(manager)))
        removers.append(_register(hass, SNOOZE_SENTENCES, _snooze_handler(manager)))
    except Exception:
        _LOGGER.warning(
            "Could not register local stop/snooze sentences; your conversation "
            "agent can still stop alarms",
            exc_info=True,
        )
        return lambda: None

    @callback
    def _update() -> None:
        if manager.ringing and not ringing:
            ringing.append(_register(hass, RINGING_STOP, _stop_handler(manager)))
            ringing.append(_register(hass, RINGING_SNOOZE, _snooze_handler(manager)))
        elif not manager.ringing and ringing:
            while ringing:
                ringing.pop()()

    removers.append(
        async_dispatcher_connect(hass, f"{SIGNAL_UPDATED}_{manager.entry.entry_id}", _update)
    )

    @callback
    def _remove() -> None:
        for remove in removers + ringing:
            remove()

    return _remove
