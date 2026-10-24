"""Compare selected build artifacts with actual decoded HTTP representations."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import json
import re
import stat
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

MAX_ASSET = 64 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
MAX_FILES = 1000
MIME = {
    ".html": ["text/html"],
    ".htm": ["text/html"],
    ".js": ["text/javascript", "application/javascript"],
    ".mjs": ["text/javascript", "application/javascript"],
    ".css": ["text/css"],
    ".json": ["application/json"],
    ".map": ["application/json"],
    ".wasm": ["application/wasm"],
    ".svg": ["image/svg+xml"],
    ".png": ["image/png"],
    ".jpg": ["image/jpeg"],
    ".jpeg": ["image/jpeg"],
    ".webp": ["image/webp"],
    ".avif": ["image/avif"],
    ".ico": ["image/x-icon", "image/vnd.microsoft.icon"],
    ".woff": ["font/woff", "application/font-woff"],
    ".woff2": ["font/woff2"],
    ".ttf": ["font/ttf"],
    ".otf": ["font/otf"],
    ".txt": ["text/plain"],
}


class InputError(ValueError):
    """Unsupported or malformed local input."""


def valid_path(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 4096
        and not any(c in value for c in "\\\x00\r\n")
        and not PurePosixPath(value).is_absolute()
        and all(p not in {"", ".", ".."} for p in value.split("/"))
        and not re.match(r"^[A-Za-z]:", value)
    )


def is_reparse(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def make_manifest(root: Path, files: list[str], overrides: dict[str, str] | None = None) -> dict:
    if not root.is_dir() or is_reparse(root):
        raise InputError("build root must be an existing non-symlink directory")
    if not files or len(files) > MAX_FILES or len(set(files)) != len(files):
        raise InputError("select 1..1000 unique files explicitly")
    overrides = overrides or {}
    if set(overrides) - set(files):
        raise InputError("MIME override does not match a selected file")
    entries = []
    for relative in sorted(files):
        if not valid_path(relative):
            raise InputError("file selection must use safe, relative POSIX paths")
        path = root
        for component in relative.split("/"):
            path = path / component
            if is_reparse(path):
                raise InputError("symlink/reparse file selection refused")
        if not path.is_file():
            raise InputError("selected asset is not a regular file")
        before = path.stat()
        if before.st_size > MAX_ASSET:
            raise InputError("selected asset exceeds 64 MiB")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                size += len(chunk)
                if size > MAX_ASSET:
                    raise InputError("selected asset exceeds 64 MiB")
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ) or size != before.st_size:
            raise InputError("asset changed while hashing")
        types = [overrides[relative]] if relative in overrides else MIME.get(path.suffix.lower())
        if not types or not all(
            re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", t) for t in types
        ):
            raise InputError("unknown extension or invalid MIME override; specify --mime path=type")
        entries.append(
            {"path": relative, "size": size, "sha256": digest.hexdigest(), "mime_types": types}
        )
    return {"schema": 1, "files": entries}


def load_manifest(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise InputError("manifest exceeds 2 MiB")
    try:
        data = json.loads(raw)
        if type(data) is not dict or type(data.get("schema")) is not int or data["schema"] != 1:
            raise ValueError
        files = data["files"]
        if type(files) is not list or not 1 <= len(files) <= MAX_FILES:
            raise ValueError
        paths = set()
        for entry in files:
            if type(entry) is not dict or not valid_path(entry["path"]) or entry["path"] in paths:
                raise ValueError
            paths.add(entry["path"])
            if type(entry["size"]) is not int or not 0 <= entry["size"] <= MAX_ASSET:
                raise ValueError
            if not isinstance(entry["sha256"], str) or not re.fullmatch(
                r"[0-9a-f]{64}", entry["sha256"]
            ):
                raise ValueError
            types = entry["mime_types"]
            if (
                type(types) is not list
                or not 1 <= len(types) <= 20
                or not all(
                    isinstance(t, str) and re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", t)
                    for t in types
                )
            ):
                raise ValueError
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError("invalid schema-1 asset manifest") from exc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_base(base: str) -> str:
    if not isinstance(base, str) or any(ord(c) <= 32 or c == "\\" for c in base):
        raise InputError("invalid base URL")
    try:
        url = urlsplit(base)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or not url.path.endswith("/")
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError
    except ValueError as exc:
        raise InputError(
            "base must be an http(s) directory URL ending /, without credentials, query or fragment"
        ) from exc
    return base


def _decode_digest(wire: bytes, encoding: str, limit: int) -> tuple[str, int]:
    source = gzip.GzipFile(fileobj=io.BytesIO(wire)) if encoding == "gzip" else io.BytesIO(wire)
    digest = hashlib.sha256()
    size = 0
    with source:
        while True:
            chunk = source.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise InputError("decoded-size-exceeds-manifest")
            digest.update(chunk)
    return digest.hexdigest(), size


def probe(entry: dict, base: str, encoding: str, timeout: float) -> dict:
    result = {"path": entry["path"], "requested_encoding": encoding, "issues": []}
    # Proxy and redirect handlers cannot forward a request to an unselected host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(  # noqa: S310 - check() validates the http(s) base before probing
        base + quote(entry["path"], safe="/"),
        headers={"Accept-Encoding": encoding, "User-Agent": "assetwire/0.1.0"},
        method="GET",
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            result["status"] = response.status
            if response.status != 200:
                result["issues"].append("http-status")
                return result
            types = response.headers.get_all("Content-Type", [])
            actual_type = types[0].split(";", 1)[0].strip().lower() if len(types) == 1 else ""
            result["mime_type"] = actual_type
            if actual_type not in entry["mime_types"]:
                result["issues"].append("mime-type")
            encodings = response.headers.get_all("Content-Encoding", [])
            actual_encoding = (
                encodings[0].strip().lower()
                if len(encodings) == 1
                else "identity"
                if not encodings
                else "unsupported"
            )
            result["content_encoding"] = actual_encoding
            if encoding == "identity" and actual_encoding != "identity":
                result["issues"].append("unexpected-content-encoding")
            if actual_encoding not in {"identity", "gzip"}:
                result["issues"].append("unsupported-content-encoding")
                return result
            lengths = response.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
                result["issues"].append("invalid-content-length")
                return result
            chunks = []
            wire_size = 0
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError
                chunk = response.read1(min(65536, MAX_ASSET + 65537 - wire_size))
                if not chunk:
                    break
                wire_size += len(chunk)
                if wire_size > MAX_ASSET + 65536:
                    result["issues"].append("wire-size-limit")
                    return result
                chunks.append(chunk)
            result["wire_size"] = wire_size
            if lengths and int(lengths[0]) != wire_size:
                result["issues"].append("content-length-mismatch")
            try:
                digest, size = _decode_digest(b"".join(chunks), actual_encoding, entry["size"])
                result.update(decoded_size=size, sha256=digest)
                if size != entry["size"]:
                    result["issues"].append("decoded-size")
                if digest != entry["sha256"]:
                    result["issues"].append("sha256")
            except InputError:
                result["issues"].append("decoded-size-exceeds-manifest")
            except (OSError, EOFError, zlib.error):
                result["issues"].append("invalid-gzip")
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["issues"].append("redirect" if 300 <= exc.code < 400 else "http-status")
        exc.close()
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
        result["issues"].append("network-error")
    return result


def check(
    manifest: dict,
    base: str,
    encodings: tuple[str, ...] = ("identity", "gzip"),
    timeout: float = 10.0,
) -> dict:
    normalize_base(base)
    if (
        not 0 < timeout <= 60
        or not encodings
        or any(x not in {"identity", "gzip"} for x in encodings)
        or len(set(encodings)) != len(encodings)
    ):
        raise InputError("timeout must be 0..60 seconds; select unique identity/gzip encodings")
    results = [
        probe(entry, base, encoding, timeout)
        for entry in manifest["files"]
        for encoding in encodings
    ]
    return {
        "schema": 1,
        "ok": not any(r["issues"] for r in results),
        "files": len(manifest["files"]),
        "checks": results,
    }
