"""Scan place/model files for asset references."""

from __future__ import annotations

import re
import time

from .client import AssetClient

PATTERNS = (
    re.compile(rb"rbxassetid://(\d+)"),
    re.compile(rb"asset/\?id=(\d+)"),
    re.compile(rb"asset%3A%2F%2F(\d+)"),
)


def extract_ids(blob: bytes) -> list[int]:
    found: list[int] = []
    for pattern in PATTERNS:
        for match in pattern.finditer(blob):
            asset_id = int(match.group(1))
            if asset_id not in found:
                found.append(asset_id)
    return found


def group_assets(
    blob: bytes,
    client: AssetClient,
    type_names: dict[int, str],
    *,
    limit: int = 0,
    delay: float = 0.1,
) -> dict[str, list[tuple[int, str]]]:
    ids = extract_ids(blob)
    if limit:
        ids = ids[:limit]

    groups: dict[str, list[tuple[int, str]]] = {}
    for index, asset_id in enumerate(ids):
        meta = client.details(asset_id)
        label = type_names.get(meta.get("AssetTypeId"), "unknown")
        groups.setdefault(label, []).append((asset_id, meta.get("Name") or ""))
        if delay and index + 1 < len(ids):
            time.sleep(delay)
    return groups
