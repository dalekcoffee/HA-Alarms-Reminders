"""What voice tools and services do. Each action returns a dict with "speech"."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import KIND_ALARM, KIND_REMINDER
from .manager import AlarmManager, Placement
from .model import (
    Item,
    ParseError,
    first_date_for,
    parse_date,
    parse_repeat,
    parse_time,
    say_day,
    say_time,
)

ALL_WORDS = {"all", "every", "everything", "all alarms", "all reminders", "all of them"}
NEXT_WORDS = {"next", "the next", "next one", "upcoming"}


def _fail(speech: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "speech": speech, **extra}


def _ok(speech: str, **extra: Any) -> dict[str, Any]:
    return {"success": True, "speech": speech, **extra}


def _place(manager: AlarmManager, room: str | None, device_id: str | None) -> Placement | str:
    if room:
        placement = manager.placement_for_area(room)
        if placement is None:
            return f"I couldn't find a voice device in a room called {room}."
        return placement
    return manager.placement_for_device(device_id)


def _where(placement: Placement) -> str:
    return f" in the {placement.area_name}" if placement.area_name else ""


def _when(
    manager: AlarmManager,
    at_text: str | None,
    on_text: str | None,
    in_minutes: Any,
    now: datetime,
) -> tuple[Any, Any] | str:
    """Return (time, date or None) or an error sentence."""
    tz = dt_util.get_default_time_zone()
    if in_minutes not in (None, ""):
        try:
            minutes = float(in_minutes)
        except (TypeError, ValueError):
            return f"I don't understand {in_minutes} minutes."
        if minutes <= 0:
            return "That time has already passed."
        when = (now + timedelta(minutes=minutes)).astimezone(tz)
        if when.second or when.microsecond:
            when = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return when.time(), when.date()
    if not at_text:
        return "missing_time"
    try:
        at = parse_time(at_text)
        on = parse_date(on_text) if on_text else None
    except ParseError as err:
        return str(err)
    return at, on


def set_item(
    manager: AlarmManager,
    kind: str,
    *,
    time: str | None = None,
    date: str | None = None,
    in_minutes: Any = None,
    repeat: Any = None,
    label: str | None = None,
    message: str | None = None,
    room: str | None = None,
    wake_up: bool | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Create an alarm or reminder."""
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    noun = "alarm" if kind == KIND_ALARM else "reminder"
    if kind == KIND_REMINDER and not (message or "").strip():
        return _fail("What should I remind you about?", needs="message")
    when = _when(manager, time, date, in_minutes, now)
    if when == "missing_time":
        if kind == KIND_REMINDER:
            day = ""
            if date:
                try:
                    day = " " + say_day(parse_date(date), now.astimezone(tz).date())
                except ParseError:
                    pass
            return _fail(f"What time{day} should I remind you?", needs="time")
        return _fail("What time should the alarm go off?", needs="time")
    if isinstance(when, str):
        return _fail(when)
    at, on = when
    try:
        days = parse_repeat(repeat)
    except ParseError as err:
        return _fail(str(err))

    placement = _place(manager, room, device_id)
    if isinstance(placement, str):
        return _fail(placement)
    if placement.satellite is None and placement.media_player is None and kind == KIND_ALARM:
        return _fail(
            "I don't know which speaker to ring. Tell me the room, or set a "
            "default voice device in the Alarms & Reminders settings.",
            needs="room",
        )

    if not days:
        on = on or first_date_for(at, now, tz)
        if datetime.combine(on, at, tzinfo=tz) <= now:
            return _fail(f"{say_time(at)} {say_day(on, now.date())} has already passed.")
    item = Item(
        kind=kind,
        at=at,
        repeat=days,
        on=None if days else on,
        label=(label or "").strip(),
        message=(message or "").strip(),
        satellite=placement.satellite,
        media_player=placement.media_player,
        wake=bool(wake_up) if wake_up is not None else False,
    )

    for sid, other in manager.items.items():
        if (
            other.kind == kind
            and other.at == item.at
            and other.repeat == item.repeat
            and other.on == item.on
            and other.satellite == item.satellite
            and (other.message or "").lower() == (item.message or "").lower()
        ):
            return _ok(
                f"You already have that {noun}: {other.describe(now, tz)}.", id=sid
            )

    sid = manager.add(item)
    how = "Recurring" if item.recurring else "One-time"
    about = f" to {item.message}" if kind == KIND_REMINDER else ""
    extra = ""
    if kind == KIND_ALARM and item.wake:
        extra = " Lights and volume will ease up when it goes off."
    return _ok(
        f"{how} {noun} set{about} for {item.describe(now, tz)}{_where(placement)}.{extra}",
        id=sid,
        recurring=item.recurring,
        room=placement.area_name,
    )


def find(manager: AlarmManager, kind: str | None, target: str | None) -> list[str]:
    """Item ids matching a spoken description."""
    candidates = [
        sid for sid, item in manager.items.items() if kind in (None, "", "all", item.kind)
    ]
    if target and target.strip() in manager.items:
        return [target.strip()]
    text = (target or "").strip().lower()
    if not text:
        return candidates if len(candidates) == 1 else []
    if text in ALL_WORDS:
        return candidates
    if text in NEXT_WORDS:
        upcoming = sorted(
            (manager.next_fires[sid], sid) for sid in candidates if sid in manager.next_fires
        )
        return [upcoming[0][1]] if upcoming else []
    time_match = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|noon|midnight)\b", text
    )
    if time_match:
        try:
            at = parse_time(time_match.group(1))
        except ParseError:
            at = None
        if at is not None:
            found = [sid for sid in candidates if manager.items[sid].at == at]
            if not found and not re.search(r"am|pm|a\.m|p\.m", text) and at.hour < 12:
                pm = at.replace(hour=at.hour + 12)
                found = [sid for sid in candidates if manager.items[sid].at == pm]
            if found:
                return found
    words = [w for w in re.split(r"\W+", text) if len(w) > 2 and w not in {"alarm", "reminder", "the", "my"}]
    return [
        sid
        for sid in candidates
        if words
        and all(
            w in (manager.items[sid].label + " " + manager.items[sid].message).lower()
            for w in words
        )
    ]


def _which(manager: AlarmManager, sids: list[str], noun: str) -> dict[str, Any]:
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    options = "; ".join(_summary(manager, sid, now, tz) for sid in sids[:6])
    return _fail(f"Which {noun} do you mean? {options}", needs="target")


def _summary(manager: AlarmManager, sid: str, now: datetime, tz: Any) -> str:
    item = manager.items[sid]
    text = item.describe(now, tz)
    if item.kind == KIND_REMINDER and item.message:
        text = f"{item.message} at {text}"
    elif item.label:
        text = f"{item.label} at {text}"
    place = manager.placement_for_satellite(item.satellite).area_name
    return text + (f" in the {place}" if place else "")


def list_items(manager: AlarmManager, kind: str | None) -> dict[str, Any]:
    """Speak the user's alarms and/or reminders, soonest first."""
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    kinds = [KIND_ALARM, KIND_REMINDER] if kind in (None, "", "all") else [kind]
    parts = []
    items_out = []
    for each in kinds:
        sids = [sid for sid, item in manager.items.items() if item.kind == each]
        sids.sort(key=lambda s: manager.next_fires.get(s) or datetime.max.replace(tzinfo=tz))
        noun = each + ("s" if len(sids) != 1 else "")
        if not sids:
            parts.append(f"You have no {each}s.")
            continue
        parts.append(
            f"You have {len(sids)} {noun}: " + "; ".join(_summary(manager, s, now, tz) for s in sids) + "."
        )
        for sid in sids:
            item = manager.items[sid]
            nxt = manager.next_fires.get(sid)
            items_out.append(
                {
                    "id": sid,
                    "kind": item.kind,
                    "description": _summary(manager, sid, now, tz),
                    "recurring": item.recurring,
                    "enabled": item.enabled,
                    "next": nxt.isoformat() if nxt else None,
                    "skipped_dates": [d.isoformat() for d in item.skip],
                }
            )
    return _ok(" ".join(parts), items=items_out)


def cancel(manager: AlarmManager, kind: str | None, target: str | None) -> dict[str, Any]:
    """Delete alarms or reminders."""
    noun = kind if kind in (KIND_ALARM, KIND_REMINDER) else "alarm or reminder"
    sids = find(manager, kind, target)
    if not sids:
        return _fail(f"I couldn't find that {noun}.")
    if len(sids) > 1 and (target or "").strip().lower() not in ALL_WORDS:
        return _which(manager, sids, noun)
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    described = [_summary(manager, sid, now, tz) for sid in sids]
    for sid in sids:
        manager.remove(sid)
    if len(sids) == 1:
        return _ok(f"Deleted: {described[0]}.")
    return _ok(f"Deleted {len(sids)} {noun}s.")


def skip(manager: AlarmManager, kind: str | None, target: str | None, date: str | None) -> dict[str, Any]:
    """Skip one day of a recurring item without deleting it."""
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    sids = [s for s in find(manager, kind or KIND_ALARM, target) if manager.items[s].recurring]
    if not sids and not target:
        sids = [s for s, i in manager.items.items() if i.recurring and i.kind == (kind or KIND_ALARM)]
    if not sids:
        return _fail("I couldn't find a repeating alarm to skip. One-time alarms can be cancelled instead.")
    if len(sids) > 1:
        if date:
            day = parse_date(date)
            sids = [s for s in sids if day.weekday() in manager.items[s].repeat] or sids
        if len(sids) > 1:
            return _which(manager, sids, "alarm")
    sid = sids[0]
    item = manager.items[sid]
    if date:
        try:
            day = parse_date(date)
        except ParseError as err:
            return _fail(str(err))
        if day.weekday() not in item.repeat:
            return _fail(f"That alarm doesn't ring on {say_day(day, now.astimezone(tz).date())}.")
    else:
        nxt = item.next_fire(now, tz)
        if nxt is None:
            return _fail("That alarm has no upcoming day to skip.")
        day = nxt.date()
    if day not in item.skip:
        item.skip.append(day)
    manager.update(sid, item)
    after = item.next_fire(now, tz)
    then = (
        f" It'll ring again {say_day(after.date(), now.astimezone(tz).date())} at {say_time(item.at)}."
        if after
        else ""
    )
    return _ok(
        f"Skipped the {say_time(item.at)} alarm {say_day(day, now.astimezone(tz).date())}.{then}"
    )


def change(
    manager: AlarmManager,
    kind: str | None,
    target: str | None,
    *,
    time: str | None = None,
    date: str | None = None,
    repeat: Any = None,
    label: str | None = None,
    message: str | None = None,
    room: str | None = None,
    wake_up: bool | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Change an existing item."""
    now = dt_util.now()
    tz = dt_util.get_default_time_zone()
    sids = find(manager, kind, target)
    noun = kind if kind in (KIND_ALARM, KIND_REMINDER) else "alarm or reminder"
    if not sids:
        return _fail(f"I couldn't find that {noun}.")
    if len(sids) > 1:
        return _which(manager, sids, noun)
    sid = sids[0]
    item = manager.items[sid]
    try:
        if time:
            item.at = parse_time(time)
        if repeat is not None:
            item.repeat = parse_repeat(repeat)
        if date:
            item.on = parse_date(date)
            item.repeat = [] if repeat is None else item.repeat
    except ParseError as err:
        return _fail(str(err))
    if not item.repeat:
        if item.on is None or (
            not date and datetime.combine(item.on, item.at, tzinfo=tz) <= now
        ):
            item.on = first_date_for(item.at, now, tz)
        if datetime.combine(item.on, item.at, tzinfo=tz) <= now:
            return _fail("That time has already passed.")
    if label is not None:
        item.label = label
    if message is not None:
        item.message = message
    if wake_up is not None:
        item.wake = wake_up
    if enabled is not None:
        item.enabled = enabled
    placement = manager.placement_for_satellite(item.satellite)
    if room:
        found = _place(manager, room, None)
        if isinstance(found, str):
            return _fail(found)
        placement = found
        item.satellite, item.media_player = found.satellite, found.media_player
    manager.update(sid, item)
    how = "recurring" if item.recurring else "one-time"
    return _ok(f"Updated. It's now a {how} {item.kind} for {item.describe(now, tz)}{_where(placement)}.")


async def stop(manager: AlarmManager) -> dict[str, Any]:
    """Stop ringing alarms."""
    count = await manager.async_stop_ringing()
    if not count:
        return _fail("No alarm is ringing.")
    return _ok("Alarm stopped.")


async def snooze(manager: AlarmManager, minutes: Any = None) -> dict[str, Any]:
    """Snooze ringing alarms."""
    try:
        mins = int(float(minutes)) if minutes not in (None, "") else None
    except (TypeError, ValueError):
        mins = None
    when = await manager.async_snooze(mins)
    if when is None:
        return _fail("No alarm is ringing.")
    return _ok(f"Snoozed until {say_time(when.time())}.")
