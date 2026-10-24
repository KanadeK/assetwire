# assetwire

Check that a static deployment serves the files you actually built, with usable MIME types.

An asset URL can return HTTP 200 and still break the site: an HTML fallback at `app.js`, old CDN bytes, incorrect JavaScript MIME, or broken gzip. `assetwire` hashes explicitly selected local build files, then checks their real HTTP representations against that manifest. No account, paid service or server agent is needed.

[简体中文](README.zh-CN.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Install and run the proof

Requires Python 3.11+; no runtime dependencies. Use a virtual environment:

```sh
git clone https://github.com/KanadeK/assetwire.git
cd assetwire
python -m pip install .
python examples/demo.py
```

The demonstration starts a real server on an ephemeral loopback port, verifies matching JavaScript under identity/gzip, then injects a 200 HTML fallback and verifies failure. It closes the server and removes temporary files. No internet requests are made by the demo. The example JS is original, synthetic fixture code.

## Audit a deployment

Generate a manifest from the release build, choosing only assets intended to be public:

```sh
assetwire manifest dist --file assets/app.js --file assets/app.css -o release-assets.json
assetwire check release-assets.json --base https://static.example.test/releases/v1/ --json
```

Replace paths and the example URL with your build and deployment. The base must end in `/`; files are appended as percent-encoded relative paths. Root and subdirectory hosting work the same way. The checker never discovers extra URLs or uploads local file contents. Each listed file causes two GET requests by default: `Accept-Encoding: identity` and `Accept-Encoding: gzip`. A server may serve identity when gzip is offered. Serving gzip despite an identity-only request is reported. Use repeated `--encoding identity` / `--encoding gzip` to select checks.

The manifest records relative path, byte size, SHA256 and allowed MIME types. Known static extensions include HTML, JS/MJS, CSS, JSON/source maps, WASM, common images and fonts, and text. MIME matching ignores parameters and case. An explicit override supports other file types:

```sh
assetwire manifest dist --file model.bin --mime model.bin=application/octet-stream -o model.json
```

Existing manifest files are refused. Store or commit the reviewed manifest with the build; creating a new baseline from a broken deployment would lose the comparison's value. These hashes detect mismatch and corruption; they do not authenticate a release if both files and manifest are replaced.

## What is checked

- HTTP 200, one matching `Content-Type`, supported `Content-Encoding`, and valid/consistent `Content-Length` when supplied.
- SHA256 and length of the decoded response, including gzip CRC/truncation errors. A same-length stale file still fails its hash check.
- Bounded encoded reads and decoding. A small gzip response cannot expand beyond the expected artifact length without failure.
- Every selected file and encoding is checked, so one failure does not conceal the rest. No response body is printed or saved.

Exit `0`: every check passed. Exit `1`: a deployment/network/representation check failed. Exit `2`: local input, schema, limit or I/O error. `python -m assetwire` also works. JSON schema 1 reports `ok`, `files`, and per-request `checks` containing relative path, encoding, status, size/hash observations and issue codes. Results contain no response bodies or base URL. Text diagnostics escape asset names.

## Deliberate limits in 0.1.1

- Selected public files only, no crawling or JS execution. This does not validate application behavior, stale service-worker clients, SRI tags, cache history, CORS, or all HTTP framing rules. Host-generated/minified content must match a deliberately chosen baseline.
- Identity and gzip only. Brotli, stacked encodings, redirects and non-200 statuses fail. No redirects are followed, even within the same origin. Use the final deployment directory URL.
- Direct HTTP(S), standard TLS certificate verification, no credentials/cookies, authentication, proxy environment variables, URL query/fragment, or TLS bypass. Run only against servers you intend to audit; the CLI does not restrict the explicitly supplied host to public IP space.
- At most 1,000 selected files, 64 MiB decoded source per file, 64 MiB + 64 KiB encoded response, and a 2 MiB manifest. Encoded data is held in bounded memory; joining chunks may temporarily duplicate it. Processing is sequential. `--timeout` defaults to 10 seconds per request, maximum 60; it is checked between reads and also used as the socket timeout, not a strict process-wide wall-clock deadline.
- UTF8 JSON manifests. Symlinks/reparse points in selected paths are refused. Use a completed, stable build directory; concurrent local file replacement is outside the safety model.

## Alternatives and motivation

[Vite PWA issue 458](https://github.com/vite-pwa/vite-plugin-pwa/issues/458) documents a blank page caused by HTML served for a stale script URL. [lychee](https://github.com/lycheeverse/lychee) is excellent for current link reachability. Browser SRI handles selected resource integrity in a running page. This tool adds an explicit build-to-HTTP byte and MIME contract, suitable for a post-deployment shell/CI check without a browser or external account. It complements local compression-sidecar validators by exercising the server response. No code from these projects is reused.

## Development and cleanup

```sh
python -m pip install -e . -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
python -m pip uninstall assetwire
```

Delete manifests you created when no longer needed. The checker writes no cache, downloads or per-user configuration. Tests use temporary synthetic files and local HTTP fixtures. CI exercises Python 3.11/3.14 on Windows, Ubuntu and macOS. [MIT](LICENSE); [third-party notes](THIRD_PARTY.md).
