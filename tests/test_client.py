import time

import requests

from rbxdl.client import AssetClient, AssetError, Auth, UrlCache


class FakeResponse:
    def __init__(self, status=200, data=None, content=b"", headers=None):
        self.status_code = status
        self._data = data
        self.content = content
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        yield self.content


class RoutedSession:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kwargs):
        for fragment, response in self.routes:
            if fragment in url:
                return response
        return FakeResponse(404, {})


def make_client(auth, routes):
    client = AssetClient(auth)
    client.session = RoutedSession(routes)
    return client


def test_opencloud_success():
    client = make_client(
        Auth(api_key="key", place_id=1),
        [("asset-delivery-api", FakeResponse(data={"location": "https://cdn/x"}))],
    )
    assert client.resolve(42) == "https://cdn/x"


def test_opencloud_denied_raises():
    client = make_client(
        Auth(api_key="key", place_id=1),
        [("asset-delivery-api", FakeResponse(data={"errors": [{"message": "denied"}]}))],
    )
    try:
        client.resolve(42)
    except AssetError as exc:
        assert "access denied" in str(exc)
    else:
        raise AssertionError("expected AssetError")


def test_denied_without_place_hints_at_place_id():
    client = make_client(
        Auth(api_key="key"),
        [("asset-delivery-api", FakeResponse(data={"errors": [{"message": "denied"}]}))],
    )
    try:
        client.resolve(42)
    except AssetError as exc:
        assert "--place-id" in str(exc)
    else:
        raise AssertionError("expected AssetError")


def test_fetch_streams_body():
    client = make_client(
        Auth(api_key="key"),
        [("cdn/x", FakeResponse(200, content=b"hello", headers={"content-type": "text/plain"}))],
    )
    body, content_type = client.fetch("https://cdn/x")
    assert body == b"hello"
    assert content_type == "text/plain"


def test_url_cache_roundtrip_and_expiry(tmp_path):
    cache = UrlCache(tmp_path / ".urls.json")
    future = int(time.time()) + 3600
    cache.put(1, f"https://cdn/x?__token__=exp={future}")
    assert cache.get(1)
    assert cache.get(3) is None

    cache.put(2, "https://cdn/x?__token__=exp=1")
    assert cache.get(2) is None
