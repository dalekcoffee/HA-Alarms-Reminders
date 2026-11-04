"""Recurrence and parsing."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.alarms_reminders.model import (
    Item,
    ParseError,
    parse_repeat,
    parse_time,
    say_repeat,
)

TZ = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("weekdays", [0, 1, 2, 3, 4]),
        ("every weekday", [0, 1, 2, 3, 4]),
        ("every weekend", [5, 6]),
        ("weekends", [5, 6]),
        ("daily", list(range(7))),
        ("every day", list(range(7))),
        ("monday, wednesday and friday", [0, 2, 4]),
        ("Tue/Thu", [1, 3]),
        ("once", []),
        (None, []),
        ([0, "sat"], [0, 5]),
    ],
)
def test_parse_repeat(text, days):
    assert parse_repeat(text) == days


def test_parse_repeat_rejects_nonsense():
    with pytest.raises(ParseError):
        parse_repeat("fortnightly")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("07:00", time(7, 0)),
        ("7am", time(7, 0)),
        ("7:30 pm", time(19, 30)),
        ("12 am", time(0, 0)),
        ("12pm", time(12, 0)),
        ("19:05:00", time(19, 5)),
        ("noon", time(12, 0)),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_say_repeat():
    assert say_repeat([0, 1, 2, 3, 4]) == "every weekday"
    assert say_repeat([5, 6]) == "every weekend"
    assert say_repeat([0, 2]) == "every Monday and Wednesday"


def test_weekday_alarm_next_fire_and_skip():
    item = Item(kind="alarm", at=time(7, 0), repeat=[0, 1, 2, 3, 4])
    friday_night = datetime(2026, 11, 6, 22, 0, tzinfo=TZ)
    assert item.next_fire(friday_night, TZ) == datetime(2026, 11, 9, 7, 0, tzinfo=TZ)
    item.skip.append(date(2026, 11, 9))
    assert item.next_fire(friday_night, TZ) == datetime(2026, 11, 10, 7, 0, tzinfo=TZ)


def test_one_time_alarm():
    item = Item(kind="alarm", at=time(7, 0), on=date(2026, 11, 5))
    assert item.next_fire(datetime(2026, 11, 4, 22, 0, tzinfo=TZ), TZ) == datetime(
        2026, 11, 5, 7, 0, tzinfo=TZ
    )
    assert item.next_fire(datetime(2026, 11, 5, 7, 0, tzinfo=TZ), TZ) is None


def test_disabled_never_fires():
    item = Item(kind="alarm", at=time(7, 0), repeat=[0], enabled=False)
    assert item.next_fire(datetime(2026, 11, 4, tzinfo=TZ), TZ) is None


def test_round_trip():
    item = Item(kind="reminder", at=time(9, 15), repeat=[6], message="take out bins", skip=[date(2026, 11, 8)])
    again = Item.from_data("reminder", item.to_data())
    assert again == item
