# Atom

Atom is a small local GUI grounding model. Give it a screenshot and a short
instruction such as "click the Save button" and it returns the point to click.
It runs entirely on your Mac; the screenshot is never sent anywhere.

What it is, stated plainly:

- A 4-bit quantized deployment of Samsung TinyClick (Florence-2-base lineage),
  about 0.27B parameters; the download is about 149 MB compressed and
  169 MB installed.
- It carries no additional gradient updates beyond the upstream weights.
- 75.9% on ScreenSpot v2 (966 of 1,272 items). This is the author's own
  evaluation and is not on a public leaderboard. The evidence is at
  <https://github.com/akshatcoder-hash/atom-screenspot-v2>.
- It grounds a single click and abstains when it cannot parse a coordinate.
  It is not a general assistant.

**Apple silicon only.** The runtime refuses to start on anything other than
macOS on arm64; its dependencies (MLX) are Metal-only.

## Install and start

```bash
./atom/install.sh     # private Python runtime + verified model download
./atom/start.sh       # API on http://127.0.0.1:8760 (ATOM_PORT to change)
```

`install.sh` is idempotent. It builds a private `uv` environment from a
hash-pinned lock file (no system Python or shell startup files are touched),
then downloads the release archive `atom-tinyclick-4bit-v1.tar.gz`, checks its
SHA-256 against the value pinned in `runtime/model-manifest.json` before
extracting anything, extracts only the expected files, and verifies every file's
byte count and SHA-256. A tampered or truncated download is refused and nothing
is installed. `start.sh` runs `install.sh` first if needed.

Defaults and overrides:

| what | default | override |
|---|---|---|
| runtime | `~/Library/Application Support/PrivateAgent/Atom/Runtime` | `ATOM_RUNTIME_DIR` |
| model | `~/Library/Application Support/PrivateAgent/Atom/Models/tinyclick-4bit` | `ATOM_MODEL_DIR` |
| port | `8760` | `ATOM_PORT` |
| model source | release archive URL in the manifest | `ATOM_MODEL_SOURCE` (`https://` or `file://` `.tar.gz`, or a directory of files) |

## HTTP contract

| method | path | purpose |
|---|---|---|
| `GET` | `/health` | model version, manifest verification, dependency versions |
| `POST` | `/ground` | `{instruction, image_base64}`; `{instruction, image_path}` only with `--allow-image-path` |
| `POST` | `/unload` | release the loaded model |

```bash
curl -s localhost:8760/ground \
  -H 'content-type: application/json' \
  -d "{\"instruction\":\"click the submit button\",\"image_base64\":\"$(base64 < shot.png | tr -d '\n')\"}"
```

```json
{"ok":true,"result":{"action":"click",
 "normalized":{"x":512.0,"y":233.5},
 "pixel":{"x":983.0,"y":421.0},
 "image":{"width":1920,"height":1080},
 "raw":"click <loc_512><loc_233>","duration_ms":359}}
```

`normalized` is in 0-1000 image space; `pixel` is in the screenshot's own
pixels. If no coordinate can be parsed the result is `{"action":"abstain",...}`.
`image_base64` accepts a bare base64 payload or a `data:image/png;base64,...`
URL. Requests are serialized, and the first `/ground` loads the model, so it is
much slower than later calls.

## Security rules

Atom has no authentication, so the server refuses anything a browser or another
local program could use to reach it by accident:

- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (blocks DNS
  rebinding), otherwise 403.
- Any request with an `Origin` header is refused with 403; no web page should
  call Atom.
- `POST` requires `Content-Type: application/json`, otherwise 415.
- `image_path` is rejected (400) unless the server is started with
  `--allow-image-path`, because it lets any local caller make Atom read
  arbitrary files. Use `image_base64`.
- Uploaded screenshots are written to a private per-process temp directory
  (mode 0700, `atom-api-<pid>-*`) and deleted as soon as the runtime returns,
  on success, error or timeout. The directory is removed on shutdown, and
  leftovers of dead servers are swept at startup.

## Warnings

- **Do not improve the prompt wrapper.** The runtime applies the wrapper
  `what to do to execute the command? {instruction}`, lowercased; it is part of
  the model's behaviour, do not change it.
- **Loopback only, no authentication.** The API binds 127.0.0.1 and has no
  auth. Do not expose it on a network without putting your own authentication in
  front. Screenshots and instruction text are untrusted input to a vision model.

## Tests

The HTTP layer and the installer's archive handling are tested without the
model or MLX, on any machine with Python 3:

```bash
python3 -m unittest discover -s atom/tests -v
```

Model accuracy is not covered by these tests.

## Layout

```
install.sh, start.sh        convenience scripts
server/atom_api.py          HTTP API over the engine (standard library only)
runtime/atom_runtime.py     stdio JSON-lines engine: health / ground / unload
runtime/install_model.py    verified installer (archive, https, file://)
runtime/model-manifest.json pinned archive hash, per-file bytes and SHA-256
runtime/bootstrap.sh        private uv venv from requirements.lock (hash-pinned)
runtime/smoke_ground.py     offline end-to-end check on a generated image
tests/                      stub runtime, HTTP contract and installer tests
```

## License

MIT for the runtime code. The model package derives from Microsoft Florence-2
and Samsung TinyClick, both MIT; their notices in [LICENSE](LICENSE) must travel
with any copy of the model. [NOTICE.md](NOTICE.md) lists the upstream
components and the open points: the copyright holder lines are reconstructed,
and the MLX conversion repository declares no license.
