"""Alarm sounds: built-in tones, uploaded files and downloaded links.

Every sound is a file in an `alarms_reminders` folder inside Home Assistant's
local media folder (`/media` on HA OS), so it also shows up under
Media › Local Media and plays through the normal media source.
"""

from __future__ import annotations

import io
import logging
import math
import re
import shutil
import struct
import wave
from collections.abc import Callable
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SOUND_FOLDER

_LOGGER = logging.getLogger(__name__)

RATE = 16000
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac", ".webm"}


class SoundError(Exception):
    """A sound could not be added."""


# --- built-in tones ---------------------------------------------------------


def _wav(samples: list[float]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(
            b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000)) for s in samples
            )
        )
    return buf.getvalue()


def _tone(freq: Callable[[float], float], seconds: float, square: bool = False) -> list[float]:
    out: list[float] = []
    phase = 0.0
    for i in range(int(seconds * RATE)):
        phase += 2 * math.pi * freq(i / RATE) / RATE
        value = math.sin(phase)
        if square:
            value = 0.8 if value >= 0 else -0.8
        out.append(value * 0.9)
    return out


def _silence(seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE)


def car_alarm() -> bytes:
    """A synthesized car-alarm cycle: rising whoops, fast warble, then chirps."""
    samples: list[float] = []
    for _ in range(3):  # slow rising whoop
        samples += _tone(lambda t: 500 + 900 * (t / 0.8), 0.8, square=True)
    samples += _tone(lambda t: 900 + 400 * math.sin(2 * math.pi * 9 * t), 2.5, square=True)
    for _ in range(6):  # alternating two-tone
        samples += _tone(lambda t: 1250, 0.18, square=True)
        samples += _tone(lambda t: 850, 0.18, square=True)
    for _ in range(8):  # chirps
        samples += _tone(lambda t: 1800, 0.08, square=True) + _silence(0.07)
    return _wav(samples)


def beeps() -> bytes:
    """Classic alarm-clock beep-beep-beep-beep."""
    samples: list[float] = []
    for _ in range(4):
        for _ in range(4):
            samples += _tone(lambda t: 2000, 0.09, square=True) + _silence(0.06)
        samples += _silence(0.5)
    return _wav(samples)


def chime() -> bytes:
    """A soft three-note chime."""
    samples: list[float] = []
    for note in (659.25, 783.99, 1046.5):
        length = int(0.6 * RATE)
        tone = _tone(lambda t, f=note: f, 0.6)
        samples += [s * math.exp(-4 * i / length) for i, s in enumerate(tone)]
    samples += _silence(0.8)
    return _wav(samples)


BUILT_IN: dict[str, tuple[str, Callable[[], bytes]]] = {
    "car_alarm.wav": ("Car alarm", car_alarm),
    "beeps.wav": ("Beeps", beeps),
    "chime.wav": ("Chime", chime),
}


# --- library ----------------------------------------------------------------


def media_root(hass: HomeAssistant) -> tuple[str, Path]:
    """(media source key, folder) for our sounds."""
    dirs = hass.config.media_dirs or {}
    key = "local" if "local" in dirs else next(iter(dirs), None)
    if key is None:
        return "local", Path(hass.config.path("media")) / SOUND_FOLDER
    return key, Path(dirs[key]) / SOUND_FOLDER


def media_id(hass: HomeAssistant, file_name: str) -> str:
    """Media source ID for a sound file."""
    key, _ = media_root(hass)
    return f"media-source://media_source/{key}/{SOUND_FOLDER}/{file_name}"


def _write_built_ins(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, (_label, make) in BUILT_IN.items():
        path = folder / name
        if not path.exists():
            path.write_bytes(make())


async def async_ensure_built_ins(hass: HomeAssistant) -> None:
    """Create the built-in sounds if they're missing."""
    _, folder = media_root(hass)
    await hass.async_add_executor_job(_write_built_ins, folder)


def safe_name(name: str, suffix: str) -> str:
    """A file name that's safe to put on disk."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:60] or "sound"
    suffix = suffix.lower() if suffix.lower() in AUDIO_SUFFIXES else ".mp3"
    return f"{stem}{suffix}"


def _unique(folder: Path, file_name: str) -> str:
    path = folder / file_name
    counter = 2
    while path.exists():
        path = folder / f"{Path(file_name).stem}_{counter}{Path(file_name).suffix}"
        counter += 1
    return path.name


def store_upload(hass: HomeAssistant, file_id: str, wanted_name: str | None) -> str:
    """Copy an uploaded file into the library. Runs in the executor."""
    from homeassistant.components.file_upload import process_uploaded_file

    _, folder = media_root(hass)
    folder.mkdir(parents=True, exist_ok=True)
    with process_uploaded_file(hass, file_id) as path:
        suffix = path.suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise SoundError("not_audio")
        file_name = _unique(folder, safe_name(wanted_name or path.name, suffix))
        shutil.copyfile(path, folder / file_name)
    return file_name


async def async_download(hass: HomeAssistant, url: str, wanted_name: str | None) -> str:
    """Download an audio file from a direct link into the library."""
    if not url.lower().startswith(("http://", "https://")):
        raise SoundError("bad_url")
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise SoundError("download_failed")
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            suffix = Path(resp.url.path).suffix.lower()
            if not ctype.startswith("audio/") and suffix not in AUDIO_SUFFIXES:
                raise SoundError("not_audio")
            body = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_DOWNLOAD_BYTES:
                    raise SoundError("too_big")
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Could not download %s: %s", url, err)
        raise SoundError("download_failed") from err

    if suffix not in AUDIO_SUFFIXES:
        suffix = {
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/flac": ".flac",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/webm": ".webm",
        }.get(ctype, ".mp3")
    name = wanted_name or Path(resp.url.path).name or "sound"

    def _save() -> str:
        _, folder = media_root(hass)
        folder.mkdir(parents=True, exist_ok=True)
        file_name = _unique(folder, safe_name(name, suffix))
        (folder / file_name).write_bytes(bytes(body))
        return file_name

    return await hass.async_add_executor_job(_save)


def delete_sound(hass: HomeAssistant, file_name: str) -> None:
    """Delete a sound file. Runs in the executor."""
    _, folder = media_root(hass)
    path = (folder / file_name).resolve()
    if path.parent != folder.resolve():
        raise SoundError("bad_name")
    path.unlink(missing_ok=True)


def sound_names(sounds: dict[str, str]) -> dict[str, str]:
    """All sounds: built-ins first, then the user's own."""
    names = {name: label for name, (label, _make) in BUILT_IN.items()}
    names.update(sounds)
    return names
