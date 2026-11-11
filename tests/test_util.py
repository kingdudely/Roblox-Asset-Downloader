from rbxdl.util import clean_name, exp_of, human_size, parse_ids, redact, sniff_ext


def test_sniff_binary_model():
    assert sniff_ext(b"<roblox!\x89\xff\r\n\x1a\n") == ".rbxm"


def test_sniff_xml_model():
    assert sniff_ext(b"<roblox version='4'>") == ".rbxmx"


def test_sniff_media():
    assert sniff_ext(b"\x89PNG\r\n\x1a\nxxx") == ".png"
    assert sniff_ext(b"OggS....") == ".ogg"
    assert sniff_ext(b"ID3....") == ".mp3"


def test_sniff_json_and_fallback():
    assert sniff_ext(b'{"a": 1}') == ".json"
    assert sniff_ext(b"\x00\x01\x02\x03", asset_type="Model") == ".rbxm"
    assert sniff_ext(b"\x00\x01\x02\x03") == ".bin"


def test_parse_ids_dedupes_and_splits():
    assert parse_ids(["1,2 3", "3", "42"]) == [1, 2, 3, 42]


def test_parse_ids_rejects_text():
    try:
        parse_ids(["abc"])
    except ValueError as exc:
        assert "not an asset id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_clean_name_is_cross_platform_safe():
    assert clean_name('a/b\\c:d*e?"f<g>h|i') == "abcdefghi"
    assert clean_name("  hello   world  ") == "hello_world"
    assert clean_name("CON") == "_CON"
    assert clean_name("") == "asset"


def test_exp_of_reads_token_and_expires():
    assert exp_of("https://x/y?__token__=exp=1234~acl=/y") == 1234
    assert exp_of("https://x/y?Expires=99&x=1") == 99
    assert exp_of("https://x/y") == 0


def test_redact_hides_token():
    assert "secret" not in redact("https://x/y?__token__=secret&a=1")


def test_human_size():
    assert human_size(999) == "999 B"
    assert human_size(2048) == "2.0 KiB"
