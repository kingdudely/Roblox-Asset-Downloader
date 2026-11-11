#!/usr/bin/env python3
"""Animation relay for Roblox experiences.

A live Roblox server cannot fetch asset delivery (HttpService blocks the required
headers and the CDN) and cannot turn .rbxm bytes into a playable clip at runtime.
This service does that work and hands the game an asset id:

    POST /api/resolve  {"placeId": 10449761463, "animationId": 77727115892579}
    ->  {"assetId": 1234567890, "cached": true}

Env:
  ROBLOX_API_KEY     required; scopes: legacy-asset:manage, asset:read,
                     asset:write, asset-permissions:write
  RELAY_TOKEN        required unless HOST is loopback; callers send
                     "Authorization: Bearer <token>"
  CREATOR_USER_ID    owner of uploaded assets (decoded from the key if omitted)
  HOST               default 127.0.0.1
  PORT               default 8080
  DRY_RUN=1          resolve + download only, skip upload/grant
  CACHE_FILE         default ./cache.json
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

LOG = logging.getLogger("relay")

KEY = os.environ.get("ROBLOX_API_KEY", "").strip()
TOKEN = os.environ.get("RELAY_TOKEN", "").strip()
CREATOR = os.environ.get("CREATOR_USER_ID", "").strip()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
DRY = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
CACHE = Path(os.environ.get("CACHE_FILE", "cache.json"))
MAX_BODY = 4096
LOCK = threading.Lock()

ASSETS = "https://apis.roblox.com"
DELIVERY = f"{ASSETS}/asset-delivery-api/v1/assetId"
UNIVERSES = f"{ASSETS}/universes/v1/places"
PERMISSIONS = f"{ASSETS}/asset-permissions-api/v1/assets/permissions"


# --------------------------------------------------------------------------
# roblox api
# --------------------------------------------------------------------------

def _headers(extra: dict | None = None) -> dict:
    headers = {"x-api-key": KEY, "Accept": "application/json"}
    headers.update(extra or {})
    return headers


def _json(method: str, url: str, **kwargs):
    extra = kwargs.pop("extra_headers", None)
    response = requests.request(method, url, headers=_headers(extra), timeout=30, **kwargs)
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text[:300]}
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code}: {json.dumps(data)[:200]}")
    return data


def cache_read() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def cache_write(data: dict) -> None:
    with LOCK:
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(CACHE)


def owner_id() -> str | None:
    """Open Cloud keys embed a JWT with the ownerId claim."""
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", KEY)
    if match:
        payload = match.group(0).split(".")[1]
        try:
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            if claims.get("ownerId"):
                return str(claims["ownerId"])
        except (ValueError, KeyError):
            pass
    return CREATOR or None


def resolve(place_id: int, animation_id: int) -> tuple[int, bool]:
    universe = _json("GET", f"{UNIVERSES}/{place_id}/universe")["universeId"]
    hit = cache_read().get(f"{animation_id}:{universe}")
    if hit:
        return int(hit["assetId"]), True

    location = _json(
        "GET",
        f"{DELIVERY}/{animation_id}",
        extra_headers={"Roblox-Place-Id": str(place_id)},
    ).get("location")
    if not location:
        raise RuntimeError("no location returned; the place may lack access to that animation")

    download = requests.get(location, timeout=60)
    download.raise_for_status()
    if download.content[:8] != b"<roblox!":
        raise RuntimeError("unexpected payload from the CDN")
    if DRY:
        return 0, False

    owner = owner_id()
    if not owner:
        raise RuntimeError("CREATOR_USER_ID is not set and could not be decoded from the key")

    upload = requests.post(
        f"{ASSETS}/assets/v1/assets",
        headers={"x-api-key": KEY},
        files={
            "request": (
                None,
                json.dumps(
                    {
                        "assetType": "Animation",
                        "displayName": f"anim_{animation_id}",
                        "description": "",
                        "creationContext": {"creator": {"userId": owner}},
                    }
                ),
                "application/json",
            ),
            "fileContent": ("animation.rbxm", download.content, "model/x-rbxm"),
        },
        timeout=120,
    )
    if upload.status_code >= 400:
        raise RuntimeError(f"upload {upload.status_code}: {upload.text[:200]}")
    operation = upload.json()

    deadline = time.time() + 180
    while time.time() < deadline and not operation.get("done"):
        time.sleep(2)
        operation = _json(
            "GET", f"{ASSETS}/assets/v1/operations/{operation['path'].split('/')[-1]}"
        )

    asset_id = (operation.get("response") or {}).get("assetId")
    if not asset_id:
        raise RuntimeError(f"upload did not finish: {json.dumps(operation)[:200]}")

    _json(
        "PATCH",
        PERMISSIONS,
        json={
            "subjectType": "Universe",
            "subjectId": str(universe),
            "action": "Use",
            "requests": [{"assetId": int(asset_id)}],
        },
    )

    data = cache_read()
    data[f"{animation_id}:{universe}"] = {"assetId": int(asset_id), "createdAt": int(time.time())}
    cache_write(data)
    return int(asset_id), False


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "rbxdl-relay/1.0"

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("%s %s", self.address_string(), fmt % args)

    def reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return not TOKEN or self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/health":
            self.reply(200, {"ok": True, "dryRun": DRY, "hasKey": bool(KEY)})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/resolve":
            self.reply(404, {"error": "not found"})
            return
        if not self.authorized():
            self.reply(401, {"error": "bad relay token"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self.reply(413, {"error": "body missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            place_id = int(body["placeId"])
            animation_id = int(body["animationId"])
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "placeId and animationId are required"})
            return
        if not KEY and not DRY:
            self.reply(500, {"error": "ROBLOX_API_KEY is not configured"})
            return

        try:
            asset_id, cached = resolve(place_id, animation_id)
        except Exception as exc:  # noqa: BLE001 - report, never crash the worker
            LOG.warning("resolve %s/%s failed: %s", place_id, animation_id, exc)
            self.reply(502, {"error": str(exc)})
            return
        self.reply(200, {"assetId": asset_id, "cached": cached, "dryRun": DRY})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not KEY and not DRY:
        LOG.error("ROBLOX_API_KEY is not set")
        return 2
    loopback = HOST in {"127.0.0.1", "::1", "localhost"}
    if not TOKEN and not loopback:
        LOG.error("refusing to listen on %s without RELAY_TOKEN", HOST)
        return 2
    if not TOKEN:
        LOG.warning("RELAY_TOKEN is unset; only safe because %s is loopback", HOST)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    LOG.info("listening on %s:%s dryRun=%s key=%s", HOST, PORT, DRY, "yes" if KEY else "no")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
