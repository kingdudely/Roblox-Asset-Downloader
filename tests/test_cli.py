import pytest

from rbxdl import __version__, cli


class StubClient:
    def details(self, asset_id):
        return {
            123: {"Name": "Thing", "AssetTypeId": 24, "Creator": {"Name": "me"}},
            111: {"Name": "Boom", "AssetTypeId": 3},
            222: {"Name": "Wave", "AssetTypeId": 24},
        }.get(asset_id, {})

    def resolve(self, asset_id):
        return f"https://cdn/{asset_id}"

    def fetch(self, url):
        return b"<roblox!data", "model/x-rbxm"


@pytest.fixture(autouse=True)
def stub_client(monkeypatch):
    monkeypatch.setattr(cli, "AssetClient", lambda *args, **kwargs: StubClient())
    monkeypatch.setenv("ROBLOX_API_KEY", "test-key")


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_help(capsys):
    assert cli.main([]) == 2
    assert "usage: rbxdl" in capsys.readouterr().out


def test_missing_key_is_an_error(monkeypatch, capsys):
    monkeypatch.delenv("ROBLOX_API_KEY")
    assert cli.main(["get", "123"]) == 2
    assert "API key is required" in capsys.readouterr().err


def test_get_writes_file(tmp_path):
    out = tmp_path / "out"
    assert cli.main(["get", "123", "-o", str(out)]) == 0
    files = list(out.glob("Thing_123.rbxm"))
    assert files and files[0].read_bytes() == b"<roblox!data"


def test_get_meta_sidecar(tmp_path):
    out = tmp_path / "out"
    assert cli.main(["get", "123", "-o", str(out), "--meta"]) == 0
    assert (out / "Thing_123.rbxm.json").exists()


def test_get_skips_existing_without_force(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "Thing_123.rbxm").write_bytes(b"old")
    assert cli.main(["get", "123", "-o", str(out)]) == 0
    assert "skip" in capsys.readouterr().out
    assert (out / "Thing_123.rbxm").read_bytes() == b"old"


def test_place_requires_target():
    assert cli.main(["place"]) == 2


def test_place_file_groups_assets(tmp_path, capsys):
    place = tmp_path / "p.rbxmx"
    place.write_bytes(
        b'<roblox><string name="SoundId">rbxassetid://111</string>'
        b'<string name="AnimationId">rbxassetid://222</string></roblox>'
    )
    assert cli.main(["place", "--file", str(place)]) == 0
    out = capsys.readouterr().out
    assert "Audio (1)" in out
    assert "Animation (1)" in out
