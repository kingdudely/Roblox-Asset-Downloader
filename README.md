# rbxdl

Download Roblox assets by id, or list every asset a place file references.
Requires an Open Cloud API key.

```bash
rbxdl get 77727115892579 -p 10449761463 -k "$ROBLOX_API_KEY"
# [77727115892579] Flexworks (Animation)
#   ok  downloads/Flexworks_77727115892579.rbxm (142,142 bytes, via opencloud)
```

## Install

```bash
pipx install .        # isolated CLI
# or
pip install .
```

Requires Python 3.10+ and `requests`. Run `rbxdl --version` to confirm.

## Auth

Create a key at **Creator Dashboard → Credentials** with the
`legacy-asset:manage` scope. Pass it with `-k`/`--api-key`, `--api-key-file`, or
`$ROBLOX_API_KEY`. The key's owner is the identity that gets checked.

There is no cookie mode. One auth path means one place for credentials to leak
and one set of scopes to reason about.

## Commands

```bash
rbxdl get <ids...> [options]     # download
rbxdl place <placeId> [options]  # list a place's asset references
rbxdl place --file MyPlace.rbxl
```

`get` options: `-p/--place-id`, `-t/--asset-type`, `-o/--out`, `--meta`,
`--force`, `--refresh`, `--no-cache`, `--dry-run`, `--delay`, `--timeout`,
`--retries`, `-v`.

Output is `downloads/<Name>_<id><ext>`, with the extension sniffed from the
bytes. Existing files are skipped unless `--force`.

### What a key + place id can reach

| asset | works | place id needed |
|---|---|---|
| public / Roblox-owned animations, images, audio, free models | yes | no |
| private animation or audio belonging to a place | yes | yes |
| private model or place file you don't own | no | — |
| paid Creator Store model | no | — |

Signed URLs are cached in `downloads/.urls.json` until their token expires
(about two days), so repeat downloads skip the API. `--refresh` bypasses the
cache.

## Find the assets a place uses

Roblox has no public "list assets by place" API. Three options:

1. **You manage the universe** — Creator Hub → Experience → Permissions →
   Assets, or `GET apis.roblox.com/asset-permissions-api/v1/universes/{id}/assets`
   (requires a Roblox session with manage access; `403 CannotManageAsset` otherwise).
2. **The place is copyable** — download the place file and scan it:
   ```bash
   rbxdl place 9391468976 -k "$KEY"
   rbxdl place --file MyPlace.rbxl --types Audio,Animation --json assets.json
   ```
3. **Runtime dump** — join the game and walk the DataModel:
   ```lua
   for _, d in ipairs(game:GetDescendants()) do
       if d:IsA("Sound") then print("SOUND", d.SoundId)
       elseif d:IsA("Animation") then print("ANIM", d.AnimationId) end
   end
   ```
   Locked places only work this way.

## Relay for in-experience use

A live Roblox server can't fetch asset delivery or turn `.rbxm` bytes into a
clip, so it asks the relay for an asset id. The relay resolves with the key,
re-uploads the animation, grants the game's universe `Use`, and caches the
result.

```bash
ROBLOX_API_KEY="$KEY" RELAY_TOKEN=secret python3 relay/server.py
# scopes: legacy-asset:manage, asset:read, asset:write, asset-permissions:write

curl -s -H 'Authorization: Bearer secret' -H 'Content-Type: application/json' \
  -d '{"placeId":10449761463,"animationId":77727115892579}' \
  http://127.0.0.1:8080/api/resolve
# {"assetId":1234567890,"cached":false,"dryRun":false}
```

Docker:

```bash
docker build -t rbxdl-relay relay/
docker run --rm -p 8080:8080 \
  -e ROBLOX_API_KEY="$KEY" -e RELAY_TOKEN=secret rbxdl-relay
```

The relay refuses to bind a non-loopback address without `RELAY_TOKEN`.
`DRY_RUN=1` resolves and downloads without uploading.

Roblox side:

```lua
local AnimationProxy = require(game.ServerScriptService.AnimationProxy)
AnimationProxy.RelayUrl = "https://relay.example.com"
AnimationProxy.RelayToken = "secret"

AnimationProxy.prefetch(10449761463, 77727115892579)   -- at server start
local track = AnimationProxy.play(humanoid, 10449761463, 77727115892579)
```

The first call uploads and can exceed one HttpService request, so warm the
cache at startup.

## Notes

- Respect Roblox's terms, asset permissions and rate limits. Paid assets,
  private models and locked place files are intentionally out of reach.
- Re-uploading other creators' animations to your account is a moderation
  risk for that account. Keep the relay private and cached.
- Never put an API key in a Roblox script.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

MIT licensed. See `LICENSE`.
