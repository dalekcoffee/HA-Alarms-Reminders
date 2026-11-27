"""Constants for Alarms & Reminders."""

from __future__ import annotations

DOMAIN = "alarms_reminders"
TITLE = "Alarms & Reminders"

KIND_ALARM = "alarm"
KIND_REMINDER = "reminder"

# Subentry data keys
CONF_TIME = "time"  # "HH:MM"
CONF_DATE = "date"  # "YYYY-MM-DD", one-time items only
CONF_REPEAT = "repeat"  # list of weekday numbers, Monday = 0
CONF_LABEL = "label"
CONF_MESSAGE = "message"
CONF_SATELLITE = "satellite"
CONF_MEDIA_PLAYER = "media_player"
CONF_SOUND = "sound"
CONF_WAKE = "wake"
CONF_LIGHTS = "lights"
CONF_ENABLED = "enabled"
CONF_SKIP = "skip_dates"
CONF_NOTIFY = "notify_phone"
CONF_SNOOZE = "snooze"
CONF_ALWAYS_RING = "always_ring"  # never send a heads-up first

# Options (integration-wide settings)
OPT_DEFAULT_SOUND = "default_sound"
OPT_RING_MINUTES = "ring_minutes"
OPT_SNOOZE_MINUTES = "snooze_minutes"
OPT_VOLUME = "volume"
OPT_START_VOLUME = "start_volume"
OPT_RAMP_MINUTES = "ramp_minutes"
OPT_WAKE_LIGHTS = "wake_lights"
OPT_LIGHT_RAMP_MINUTES = "light_ramp_minutes"
OPT_BRIGHTNESS = "brightness"
OPT_KELVIN = "kelvin"
OPT_NOTIFY_TARGETS = "notify_targets"
OPT_NOTIFY_ALARMS = "notify_alarms"
OPT_DEFAULT_SATELLITE = "default_satellite"
OPT_SOUNDS = "sounds"  # {file name: display name}
OPT_VACATION = "vacation"  # bool: alarms are off
OPT_VACATION_END = "vacation_end"  # "YYYY-MM-DD", last day of vacation
OPT_HEADS_UP = "heads_up"  # bool: phone heads-up before alarms
OPT_HEADS_UP_MINUTES = "heads_up_minutes"
OPT_HEADS_UP_FOLLOW_UP = "heads_up_follow_up_only"  # only after an alarm was stopped
OPT_HEADS_UP_WINDOW = "heads_up_window_minutes"

DEFAULT_RING_MINUTES = 10
DEFAULT_SNOOZE_MINUTES = 9
DEFAULT_VOLUME = 70
DEFAULT_START_VOLUME = 10
DEFAULT_RAMP_MINUTES = 10
DEFAULT_LIGHT_RAMP_MINUTES = 10
DEFAULT_BRIGHTNESS = 100
DEFAULT_KELVIN = 6500
DEFAULT_SOUND = "car_alarm.wav"
DEFAULT_HEADS_UP_MINUTES = 5
DEFAULT_HEADS_UP_WINDOW = 60
DEFAULT_HEADS_UP_FOLLOW_UP = False  # heads-up before every alarm

# One-time alarms missed while Home Assistant was down still ring if they
# are at most this late.
MISSED_GRACE_MINUTES = 10

SOUND_FOLDER = "alarms_reminders"

SIGNAL_UPDATED = f"{DOMAIN}_updated"
SIGNAL_NEW_ITEM = f"{DOMAIN}_new_item"

EVENT_RINGING = f"{DOMAIN}_ringing"
EVENT_STOPPED = f"{DOMAIN}_stopped"
EVENT_REMINDER = f"{DOMAIN}_reminder"
EVENT_SKIPPED = f"{DOMAIN}_skipped"
