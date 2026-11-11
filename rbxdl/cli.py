"""Command line interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

import requests

from . import __version__
from .client import AssetClient, AssetError, Auth, UrlCache
from .place import extract_ids, group_assets
from .types import ASSET_TYPES
from .util import atomic_write, clean_name, human_size, parse_ids, read_secret, sniff_ext

LOG = logging.getLogger("rbxdl")

EPILOG = """examples:
  rbxdl get 77727115892579 -p 10449761463 -k "$ROBLOX_API_KEY"
  rbxdl get 81170818353676 -p 9391468976 -k "$ROBLOX_API_KEY"
  rbxdl place 9391468976 -k "$ROBLOX_API_KEY"
  rbxdl place --file MyPlace.rbxl --types Audio,Animation
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--place-id", type=int, help="place id used as the delivery context")
    common.add_argument("-k", "--api-key", help="Open Cloud API key (legacy-asset:manage)")
    common.add_argument("--api-key-file", help="file containing an API key")
    common.add_argument("--timeout", type=float, default=30.0, help="request timeout (seconds)")
    common.add_argument("--retries", type=int, default=3, help="retries for transient errors")
    common.add_argument("-v", "--verbose", action="store_true", help="log HTTP details (tokens redacted)")

    parser = argparse.ArgumentParser(
        prog="rbxdl",
        description="Download Roblox assets, or list the assets a place file uses.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"rbxdl {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{get,place}")

    get = sub.add_parser("get", parents=[common], help="download one or more assets")
    get.add_argument("ids", nargs="+", help="asset ids (space or comma separated)")
    get.add_argument("-o", "--out", default="downloads", help="output directory (default: downloads)")
    get.add_argument("-t", "--asset-type", help="type hint, e.g. Animation, Audio, Model")
    get.add_argument("--meta", action="store_true", help="write a .json sidecar per asset")
    get.add_argument("--force", action="store_true", help="overwrite existing files")
    get.add_argument("--refresh", action="store_true", help="ignore cached signed URLs")
    get.add_argument("--no-cache", action="store_true", help="do not read or write the URL cache")
    get.add_argument("--dry-run", action="store_true", help="resolve only, write nothing")
    get.add_argument("--delay", type=float, default=0.2, help="pause between assets (seconds)")

    place = sub.add_parser("place", parents=[common], help="list assets referenced by a place")
    place.add_argument("place_id", nargs="?", type=int, help="place id to download and scan")
    place.add_argument("--file", help="scan a local .rbxl/.rbxm/.rbxmx instead")
    place.add_argument("--types", help="filter, e.g. Audio,Animation")
    place.add_argument("--json", metavar="FILE", help="write the result as JSON")
    place.add_argument("--limit", type=int, default=0, help="scan at most N references")
    return parser


def build_auth(args: argparse.Namespace) -> Auth:
    key = args.api_key or os.environ.get("ROBLOX_API_KEY")
    if not key and args.api_key_file:
        key = read_secret(args.api_key_file)
    if not key:
        raise ValueError(
            "an API key is required: use --api-key, --api-key-file or $ROBLOX_API_KEY"
        )
    return Auth(api_key=key, place_id=args.place_id)


def _client(args: argparse.Namespace, auth: Auth) -> AssetClient:
    return AssetClient(auth, timeout=args.timeout, retries=args.retries, verbose=args.verbose)


def cmd_get(args: argparse.Namespace) -> int:
    try:
        ids = parse_ids(args.ids)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not ids:
        print("error: no asset ids given", file=sys.stderr)
        return 2

    auth = build_auth(args)
    client = _client(args, auth)

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    cache = UrlCache(out / ".urls.json", enabled=not args.no_cache)

    failures = 0
    for index, asset_id in enumerate(ids):
        try:
            meta = client.details(asset_id)
            asset_type = args.asset_type or ASSET_TYPES.get(meta.get("AssetTypeId"))
            name = meta.get("Name") or f"asset_{asset_id}"
            print(f"[{asset_id}] {name} ({asset_type or 'unknown'})")

            if args.dry_run:
                cached = cache.get(asset_id)
                source = "cache" if cached else "opencloud"
                print(f"  would fetch via {source}")
                continue

            url = None if args.refresh else cache.get(asset_id)
            source = "cache"
            if not url:
                url = client.resolve(asset_id)
                cache.put(asset_id, url)
                source = "opencloud"

            body, content_type = client.fetch(url)
            extension = sniff_ext(body, content_type, asset_type)
            path = out / f"{clean_name(name)}_{asset_id}{extension}"

            if path.exists() and not args.force:
                print(f"  skip {path.name} (exists; use --force)")
            else:
                atomic_write(path, body)
                print(f"  ok  {path} ({human_size(len(body))}, via {source})")
                if args.meta:
                    sidecar = path.with_suffix(path.suffix + ".json")
                    sidecar.write_text(
                        json.dumps(
                            {
                                "assetId": asset_id,
                                "name": name,
                                "assetType": asset_type,
                                "creator": (meta.get("Creator") or {}).get("Name"),
                                "resolvedVia": source,
                                "bytes": len(body),
                                "sha256": hashlib.sha256(body).hexdigest(),
                                "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            },
                            indent=2,
                        )
                        + "\n"
                    )
        except AssetError as exc:
            failures += 1
            print(f"  error: {exc}", file=sys.stderr)
        except requests.RequestException as exc:
            failures += 1
            print(f"  network error: {exc}", file=sys.stderr)

        if args.delay and index + 1 < len(ids):
            time.sleep(args.delay)

    return 1 if failures else 0


def cmd_place(args: argparse.Namespace) -> int:
    if not args.file and not args.place_id:
        print("error: give a place id or --file", file=sys.stderr)
        return 2

    auth = build_auth(args)

    if args.file:
        try:
            blob = Path(args.file).read_bytes()
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        source = args.file
        client = _client(args, auth)
    else:
        target = args.place_id
        auth = replace(auth, place_id=auth.place_id or target)
        client = _client(args, auth)
        try:
            url = client.resolve(target)
            blob, _ = client.fetch(url)
        except (AssetError, requests.RequestException) as exc:
            print(f"place {target}: {exc}", file=sys.stderr)
            print(
                "place files only download when copying is enabled or you own the place.\n"
                "otherwise dump at runtime: game:GetDescendants() inside the game.",
                file=sys.stderr,
            )
            return 2
        source = f"place {target} (via opencloud)"

    ids = extract_ids(blob)
    print(f"{source}: {len(ids)} asset references")

    groups = group_assets(blob, client, ASSET_TYPES, limit=args.limit)
    wanted = {t.strip().lower() for t in (args.types or "").split(",") if t.strip()}
    for label in sorted(groups, key=lambda key: (-len(groups[key]), key)):
        if wanted and label.lower() not in wanted:
            continue
        print(f"\n{label} ({len(groups[label])})")
        for asset_id, name in groups[label]:
            print(f"  {asset_id}  {name}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "source": source,
                    "references": len(ids),
                    "groups": {
                        label: [{"assetId": aid, "name": name} for aid, name in items]
                        for label, items in groups.items()
                    },
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nwrote {args.json}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        build_parser().print_help()
        return 2

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )
    if args.verbose:
        logging.getLogger("rbxdl").setLevel(logging.DEBUG)

    try:
        if args.command == "get":
            return cmd_get(args)
        return cmd_place(args)
    except ValueError as exc:  # bad secret file, etc.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
