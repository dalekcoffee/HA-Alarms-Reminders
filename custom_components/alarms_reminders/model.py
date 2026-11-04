"""Alarm and reminder data, recurrence and wording.

Pure Python with no Home Assistant imports, so the scheduling rules are easy
to test. Every alarm and reminder is stored as a config subentry whose data
is the dict described by `Item`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from .const import (
    CONF_DATE,
    CONF_ENABLED,
    CONF_LABEL,
    CONF_LIGHTS,
    CONF_MEDIA_PLAYER,
    CONF_MESSAGE,
    CONF_NOTIFY,
    CONF_REPEAT,
    CONF_SATELLITE,
    CONF_SKIP,
    CONF_SNOOZE,
    CONF_SOUND,
    CONF_TIME,
    CONF_WAKE,
    KIND_ALARM,
    KIND_REMINDER,
)

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]
EVERY_DAY = list(range(7))

_DAY_ALIASES: dict[str, int] = {}
for _i, _name in enumerate(DAY_NAMES):
    for _alias in (_name, _name[:3], _name[:2], _name + "s"):
        _DAY_ALIASES[_alias] = _i
_DAY_ALIASES.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})

_GROUPS: dict[str, list[int]] = {
    "weekday": WEEKDAYS,
    "weekdays": WEEKDAYS,
    "workday": WEEKDAYS,
    "workdays": WEEKDAYS,
    "weekend": WEEKEND,
    "weekends": WEEKEND,
    "daily": EVERY_DAY,
    "day": EVERY_DAY,
    "days": EVERY_DAY,
    "everyday": EVERY_DAY,
    "all": EVERY_DAY,
}

_NO_REPEAT = {"", "none", "no", "once", "one time", "one-time", "never", "false"}


class ParseError(ValueError):
    """A spoken value could not be understood."""


def parse_repeat(value: Any) -> list[int]:
    """Turn "weekdays", "every weekend", "mon, wed" or [0, 2] into weekday numbers.

    An empty value means the item happens once.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        days: set[int] = set()
        for part in value:
            days.update(parse_repeat(part))
        return sorted(days)
    if isinstance(value, int):
        if 0 <= value <= 6:
            return [value]
        raise ParseError(f"Not a weekday: {value}")
    text = str(value).strip().lower()
    if text.isdigit():
        return parse_repeat(int(text))
    if text in _NO_REPEAT:
        return []
    text = re.sub(r"\b(every|each|on|the|and|only|at|of|week)\b", " ", text)
    text = text.replace("every day", "daily")
    days = set()
    for word in re.split(r"[\s,;/&+]+", text):
        word = word.strip(".")
        if not word:
            continue
        if word in _GROUPS:
            days.update(_GROUPS[word])
        elif word in _DAY_ALIASES:
            days.add(_DAY_ALIASES[word])
        else:
            raise ParseError(f"I don't understand the repeat value '{value}'")
    if not days:
        raise ParseError(f"I don't understand the repeat value '{value}'")
    return sorted(days)


def parse_time(value: Any) -> time:
    """Parse "07:00", "7:30 pm", "7am", "19:05:00" or "noon"."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip().lower().replace(".", "")
    if text in ("noon", "midday"):
        return time(12, 0)
    if text == "midnight":
        return time(0, 0)
    match = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?(?::\d{2})?\s*(am|pm|a|p)?", text)
    if not match:
        raise ParseError(f"I don't understand the time '{value}'")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix:
        if not 1 <= hour <= 12:
            raise ParseError(f"I don't understand the time '{value}'")
        hour = hour % 12 + (12 if suffix.startswith("p") else 0)
    if hour > 23 or minute > 59:
        raise ParseError(f"I don't understand the time '{value}'")
    return time(hour, minute)


def parse_date(value: Any) -> date:
    """Parse an ISO date ("2026-11-06")."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as err:
        raise ParseError(f"I don't understand the date '{value}'") from err


def say_time(value: time) -> str:
    """7:00 AM style."""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def say_repeat(days: Iterable[int]) -> str:
    """'every weekday', 'every Monday and Wednesday'."""
    days = sorted(set(days))
    if days == EVERY_DAY:
        return "every day"
    if days == WEEKDAYS:
        return "every weekday"
    if days == WEEKEND:
        return "every weekend"
    names = [DAY_NAMES[d].capitalize() for d in days]
    if len(names) == 1:
        return f"every {names[0]}"
    return "every " + ", ".join(names[:-1]) + " and " + names[-1]


def say_day(day: date, today: date) -> str:
    """'today', 'tomorrow', 'Friday', 'Friday, November 13'."""
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    name = DAY_NAMES[day.weekday()].capitalize()
    if 1 < delta < 7:
        return name
    return f"{name}, {day.strftime('%B')} {day.day}"


@dataclass
class Item:
    """One alarm or reminder."""

    kind: str
    at: time
    repeat: list[int] = field(default_factory=list)
    on: date | None = None
    label: str = ""
    message: str = ""
    satellite: str | None = None
    media_player: str | None = None
    sound: str | None = None
    wake: bool = False
    lights: list[str] = field(default_factory=list)
    enabled: bool = True
    skip: list[date] = field(default_factory=list)
    notify_phone: bool = True
    snooze: bool = False

    @property
    def recurring(self) -> bool:
        """Whether it repeats."""
        return bool(self.repeat)

    @classmethod
    def from_data(cls, kind: str, data: Mapping[str, Any]) -> Item:
        """Build from stored subentry data."""
        on = data.get(CONF_DATE)
        return cls(
            kind=kind,
            at=parse_time(data[CONF_TIME]),
            repeat=sorted(set(data.get(CONF_REPEAT) or [])),
            on=parse_date(on) if on else None,
            label=data.get(CONF_LABEL) or "",
            message=data.get(CONF_MESSAGE) or "",
            satellite=data.get(CONF_SATELLITE) or None,
            media_player=data.get(CONF_MEDIA_PLAYER) or None,
            sound=data.get(CONF_SOUND) or None,
            wake=bool(data.get(CONF_WAKE, False)),
            lights=list(data.get(CONF_LIGHTS) or []),
            enabled=bool(data.get(CONF_ENABLED, True)),
            skip=[parse_date(d) for d in data.get(CONF_SKIP) or []],
            notify_phone=bool(data.get(CONF_NOTIFY, True)),
            snooze=bool(data.get(CONF_SNOOZE, False)),
        )

    def to_data(self) -> dict[str, Any]:
        """Serialise for subentry storage."""
        data: dict[str, Any] = {
            CONF_TIME: self.at.strftime("%H:%M"),
            CONF_REPEAT: sorted(set(self.repeat)),
            CONF_DATE: self.on.isoformat() if self.on and not self.repeat else None,
            CONF_ENABLED: self.enabled,
            CONF_SKIP: sorted(d.isoformat() for d in set(self.skip)),
            CONF_SATELLITE: self.satellite,
            CONF_MEDIA_PLAYER: self.media_player,
        }
        if self.kind == KIND_ALARM:
            data.update(
                {
                    CONF_LABEL: self.label,
                    CONF_SOUND: self.sound,
                    CONF_WAKE: self.wake,
                    CONF_LIGHTS: list(self.lights),
                    CONF_SNOOZE: self.snooze,
                }
            )
        else:
            data.update({CONF_MESSAGE: self.message, CONF_NOTIFY: self.notify_phone})
        return data

    def next_fire(self, after: datetime, tz: tzinfo) -> datetime | None:
        """The first time strictly after `after` that this goes off."""
        if not self.enabled:
            return None
        if not self.repeat:
            if self.on is None:
                return None
            when = datetime.combine(self.on, self.at, tzinfo=tz)
            return when if when > after else None
        start = after.astimezone(tz).date()
        for offset in range(15):
            day = start + timedelta(days=offset)
            if day.weekday() not in self.repeat or day in self.skip:
                continue
            when = datetime.combine(day, self.at, tzinfo=tz)
            if when > after:
                return when
        return None

    def title(self) -> str:
        """Name shown in the UI list."""
        when = say_time(self.at)
        if self.repeat:
            when += ", " + say_repeat(self.repeat).removeprefix("every ")
        elif self.on:
            when += f", {DAY_SHORT[self.on.weekday()]} {self.on.strftime('%b')} {self.on.day}"
        text = self.message if self.kind == KIND_REMINDER else self.label
        prefix = "Snooze" if self.snooze else self.kind.capitalize()
        return f"{prefix} {when}" + (f" – {text}" if text else "")

    def describe(self, now: datetime, tz: tzinfo) -> str:
        """Spoken summary: '7:00 AM every weekday' or '7:00 AM tomorrow (one time)'."""
        when = say_time(self.at)
        if self.repeat:
            text = f"{when} {say_repeat(self.repeat)}"
        else:
            day = self.on or now.astimezone(tz).date()
            text = f"{when} {say_day(day, now.astimezone(tz).date())}"
        if not self.enabled:
            text += " (turned off)"
        return text


def first_date_for(at: time, now: datetime, tz: tzinfo) -> date:
    """The next date a one-time item at this time can ring: today if still ahead."""
    local = now.astimezone(tz)
    if datetime.combine(local.date(), at, tzinfo=tz) > now:
        return local.date()
    return local.date() + timedelta(days=1)


def prune_skips(item: Item, today: date) -> bool:
    """Drop skip dates in the past. Returns True when something changed."""
    kept = [d for d in item.skip if d >= today]
    changed = len(kept) != len(item.skip)
    item.skip = kept
    return changed


def kind_of(item_kind: str) -> str:
    """Validate a kind."""
    if item_kind not in (KIND_ALARM, KIND_REMINDER):
        raise ParseError(f"Unknown kind {item_kind}")
    return item_kind
