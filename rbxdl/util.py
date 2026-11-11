"""Small helpers: naming, type sniffing, ids, atomic files."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

MAGIC = b"<roblox!"
MAX_BYTES = 256 * 1024 * 1024

_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_ID_RE = re.compile(r"[,\s]+")


def clean_name(name: str, fallback: str = "asset") -> str:
    """Make a string safe as a file name on Windows, macOS and Linux."""
    name = re.sub(r'[\\/*?"<>|:\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", "_", name.strip()).strip("._")
    name = name[:96] or fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    return name


def parse_ids(values: Iterable[str]) -> list[int]:
    ids: list[int] = []
    for chunk in values:
        for part in _ID_RE.split((chunk or "").strip()):
            if not part:
                continue
            if not part.isdigit():
                raise ValueError(f"not an asset id: {part!r}")
            value = int(part)
            if value not in ids:
                ids.append(value)
    return ids


def sniff_ext(body: bytes, content_type: str = "", asset_type: Optional[str] = None) -> str:
    head = body[:16]
    checks = (
        (body[:8] == MAGIC, ".rbxm"),
        (body.lstrip()[:7] == b"<roblox", ".rbxmx"),
        (head.startswith(b"\x89PNG"), ".png"),
        (head.startswith(b"\xff\xd8\xff"), ".jpg"),
        (head.startswith(b"GIF8"), ".gif"),
        (head.startswith(b"BM"), ".bmp"),
        (head.startswith(b"OggS"), ".ogg"),
        (head.startswith(b"RIFF") and body[8:12] == b"WAVE", ".wav"),
        (head.startswith(b"fLaC"), ".flac"),
        (head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"), ".mp3"),
        (len(body) > 12 and body[4:8] == b"ftyp", ".mov" if body[8:12] == b"qt  " else ".mp4"),
        (head.startswith(b"\x00\x01\x00\x00"), ".ttf"),
        (head.startswith(b"OTTO"), ".otf"),
        (head.startswith(b"wOFF"), ".woff"),
        (head.startswith(b"wOF2"), ".woff2"),
    )
    for matched, ext in checks:
        if matched:
            return ext

    stripped = body.lstrip()
    if stripped[:1] in (b"{", b"[") and len(body) < 1_000_000:
        try:
            json.loads(stripped)
            return ".json"
        except ValueError:
            pass

    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "video/mp4": ".mp4",
        "model/x-rbxm": ".rbxm",
        "model/x-rbxmx": ".rbxmx",
    }.get(content_type or "", {
        "Image": ".png", "Decal": ".png", "Audio": ".ogg", "Video": ".mp4",
        "Animation": ".rbxm", "Model": ".rbxm", "SolidModel": ".rbxm",
        "MeshPart": ".rbxm", "Plugin": ".rbxm", "Package": ".rbxm",
        "Lua": ".lua", "Text": ".txt", "HTML": ".html", "FontFamily": ".json",
        "LocalizationTable": ".csv",
    }.get(asset_type or "", ".bin"))


def exp_of(url: str) -> int:
    match = re.search(r"exp=(\d+)", url) or re.search(r"Expires=(\d+)", url)
    return int(match.group(1)) if match else 0


def redact(url: str) -> str:
    """Hide signed-token values before logging a URL."""
    return re.sub(r"(__token__=)[^&]*", r"\1<redacted>", url)


def read_secret(path: os.PathLike | str) -> str:
    file = Path(path).expanduser()
    lines = [line.strip() for line in file.read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise ValueError(f"{file} is empty")
    if os.name == "posix" and file.stat().st_mode & 0o077:
        print(
            f"warning: {file} is readable by other users; chmod 600 it",
            file=sys.stderr,
        )
    return lines[-1]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def human_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:,} B"
