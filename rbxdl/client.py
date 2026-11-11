"""Open Cloud HTTP client for Roblox asset delivery."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .util import MAX_BYTES, exp_of, load_json, redact, save_json

LOG = logging.getLogger("rbxdl")

OPENCLOUD = "https://apis.roblox.com"
ECONOMY = "https://economy.roblox.com"


class AssetError(RuntimeError):
    """The asset could not be resolved or downloaded."""


@dataclass(frozen=True)
class Auth:
    api_key: str
    place_id: Optional[int] = None


class AssetClient:
    def __init__(
        self,
        auth: Auth,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        verbose: bool = False,
    ) -> None:
        self.auth = auth
        self.timeout = timeout
        self.verbose = verbose
        self.session = requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _log(self, message: str, *args) -> None:
        if self.verbose:
            LOG.debug(message, *args)

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error(data: dict) -> str:
        errors = data.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message") or errors[0])
        return ""

    def resolve(self, asset_id: int) -> str:
        """Return a signed URL for the asset, or raise AssetError."""
        headers = {"x-api-key": self.auth.api_key, "Accept": "application/json"}
        if self.auth.place_id:
            headers["Roblox-Place-Id"] = str(self.auth.place_id)

        response = self.session.get(
            f"{OPENCLOUD}/asset-delivery-api/v1/assetId/{asset_id}",
            headers=headers,
            timeout=self.timeout,
        )
        data = self._json(response)
        if data.get("location"):
            return data["location"]

        message = self._error(data) or f"HTTP {response.status_code}"
        hint = ""
        if not self.auth.place_id:
            hint = " (pass --place-id for private animations or audio)"
        raise AssetError(f"access denied: {message}{hint}")

    def fetch(self, url: str) -> tuple[bytes, str]:
        self._log("GET %s", redact(url))
        response = self.session.get(url, timeout=max(self.timeout, 60), stream=True)
        response.raise_for_status()

        length = int(response.headers.get("content-length") or 0)
        if length > MAX_BYTES:
            raise AssetError(f"asset is too large ({length} bytes)")

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                raise AssetError("asset exceeds the size limit")
            chunks.append(chunk)

        if not chunks:
            raise AssetError("empty response body")
        return b"".join(chunks), response.headers.get("content-type", "")

    def details(self, asset_id: int) -> dict:
        try:
            response = self.session.get(
                f"{ECONOMY}/v2/assets/{asset_id}/details", timeout=self.timeout
            )
            if response.status_code == 200:
                return self._json(response)
        except requests.RequestException:
            pass
        return {}


class UrlCache:
    """Signed CDN URLs, reusable until their token expires."""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._data = load_json(path, {}) if enabled else {}

    def get(self, asset_id: int) -> Optional[str]:
        if not self.enabled:
            return None
        entry = self._data.get(str(asset_id)) or {}
        url = entry.get("url")
        if not url:
            return None
        if (entry.get("exp") or exp_of(url)) < time.time() + 60:
            return None
        return url

    def put(self, asset_id: int, url: str) -> None:
        if not self.enabled:
            return
        self._data[str(asset_id)] = {"url": url, "exp": exp_of(url)}
        try:
            save_json(self.path, self._data)
        except OSError:
            pass
