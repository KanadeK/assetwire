import contextlib
import gzip
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from assetwire.cli import main
from assetwire.core import InputError, check, load_manifest, make_manifest, normalize_base


class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(self.path)
                body = cls.payload
                self.send_response(cls.status)
                for name, value in cls.headers:
                    self.send_header(name, value)
                if cls.gzip_body:
                    body = gzip.compress(body)
                    if cls.truncate:
                        body = body[:-4]
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if cls.delay:
                    time.sleep(cls.delay)
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/docs/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body = b"export const value = 42;\n"
        (self.root / "app.js").write_bytes(self.body)
        cls = type(self)
        cls.payload, cls.status = self.body, 200
        cls.headers = [("Content-Type", "text/javascript; charset=utf-8")]
        cls.gzip_body = cls.truncate = False
        cls.delay = 0
        cls.requests.clear()
        self.manifest = make_manifest(self.root, ["app.js"])

    def test_identity_and_gzip_offers_accept_matching_identity(self):
        result = check(self.manifest, self.base)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["checks"]), 2)

    def test_real_gzip_decodes_to_source_digest(self):
        type(self).gzip_body = True
        result = check(self.manifest, self.base, ("gzip",))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"][0]["sha256"], hashlib.sha256(self.body).hexdigest())

    def test_http_200_html_fallback_fails(self):
        type(self).payload = b"<html>App fallback</html>"
        type(self).headers = [("Content-Type", "text/html")]
        report = check(self.manifest, self.base, ("identity",))
        self.assertFalse(report["ok"])
        self.assertIn("mime-type", report["checks"][0]["issues"])

    def test_stale_same_size_js_fails_sha(self):
        type(self).payload = self.body.replace(b"42", b"41")
        result = check(self.manifest, self.base, ("identity",))
        self.assertEqual(result["checks"][0]["issues"], ["sha256"])

    def test_wrong_mime_with_correct_bytes_fails(self):
        type(self).headers = [("Content-Type", "text/plain")]
        self.assertEqual(
            check(self.manifest, self.base, ("identity",))["checks"][0]["issues"], ["mime-type"]
        )

    def test_redirect_is_not_followed(self):
        type(self).status = 302
        type(self).headers = [("Location", "/not-selected")]
        result = check(self.manifest, self.base, ("identity",))
        self.assertEqual(result["checks"][0]["issues"], ["redirect"])
        self.assertEqual(type(self).requests, ["/docs/app.js"])

    def test_404_and_206_fail(self):
        for status in [404, 206]:
            type(self).status = status
            self.assertEqual(
                check(self.manifest, self.base, ("identity",))["checks"][0]["issues"],
                ["http-status"],
            )

    def test_truncated_gzip_fails(self):
        type(self).gzip_body = type(self).truncate = True
        self.assertIn(
            "invalid-gzip", check(self.manifest, self.base, ("gzip",))["checks"][0]["issues"]
        )

    def test_gzip_expansion_bound(self):
        type(self).gzip_body = True
        type(self).payload = b"x" * 100_000
        self.assertEqual(
            check(self.manifest, self.base, ("gzip",))["checks"][0]["issues"],
            ["decoded-size-exceeds-manifest"],
        )

    def test_unsupported_encoding_and_duplicate_type(self):
        type(self).headers += [("Content-Encoding", "br")]
        self.assertIn(
            "unsupported-content-encoding",
            check(self.manifest, self.base, ("identity",))["checks"][0]["issues"],
        )
        type(self).headers = [("Content-Type", "text/javascript"), ("Content-Type", "text/html")]
        self.assertIn(
            "mime-type", check(self.manifest, self.base, ("identity",))["checks"][0]["issues"]
        )

    def test_network_deadline(self):
        type(self).delay = 0.15
        self.assertIn(
            "network-error",
            check(self.manifest, self.base, ("identity",), timeout=0.03)["checks"][0]["issues"],
        )

    def test_bounded_wire_read(self):
        type(self).payload = b"x" * 70000
        with patch("assetwire.core.MAX_ASSET", 1):
            result = check(self.manifest, self.base, ("identity",))
        self.assertIn("wire-size-limit", result["checks"][0]["issues"])

    def test_gzip_ignoring_identity_request_is_reported(self):
        type(self).gzip_body = True
        self.assertIn(
            "unexpected-content-encoding",
            check(self.manifest, self.base, ("identity",))["checks"][0]["issues"],
        )

    def test_arbitrary_path_characters_are_percent_encoded(self):
        (self.root / "文 #%.js").write_bytes(self.body)
        result = check(make_manifest(self.root, ["文 #%.js"]), self.base, ("identity",))
        self.assertTrue(result["ok"])
        self.assertEqual(type(self).requests, ["/docs/%E6%96%87%20%23%25.js"])

    def test_manifest_has_no_absolute_path_or_file_contents(self):
        text = json.dumps(self.manifest)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("export const", text)

    def test_selection_does_not_include_unrequested_private_files(self):
        (self.root / ".env").write_text("PRIVATE", encoding="utf-8")
        self.assertEqual(len(make_manifest(self.root, ["app.js"])["files"]), 1)

    def test_unsafe_selections_rejected(self):
        for files in [[], ["../app.js"], ["/app.js"], ["a\\b.js"], ["app.js", "app.js"]]:
            with self.subTest(files=files), self.assertRaises(InputError):
                make_manifest(self.root, files)

    def test_unknown_extension_requires_explicit_type(self):
        (self.root / "payload.bin").write_bytes(self.body)
        with self.assertRaises(InputError):
            make_manifest(self.root, ["payload.bin"])
        self.assertEqual(
            make_manifest(self.root, ["payload.bin"], {"payload.bin": "application/octet-stream"})[
                "files"
            ][0]["mime_types"],
            ["application/octet-stream"],
        )

    def test_invalid_base_and_limits(self):
        for base in [
            "file:///tmp/",
            "http://user:pass@example.test/",
            "https://example.test/docs",
            "https://example.test/?secret=1",
            "http://example.test:99999/",
            "https://example.test/\n",
        ]:
            with self.subTest(base=base), self.assertRaises(InputError):
                normalize_base(base)
        with self.assertRaises(InputError):
            check(self.manifest, self.base, timeout=0)
        with self.assertRaises(InputError):
            check(self.manifest, self.base, ("gzip", "gzip"))

    def test_manifest_input_schema_rejects_malformation(self):
        for content in [
            "{}",
            "[]",
            "{",
            json.dumps({**self.manifest, "schema": True}),
            json.dumps({"schema": 1, "files": self.manifest["files"] * 2}),
        ]:
            path = self.root / "bad.json"
            path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(InputError):
                load_manifest(path)

    def test_cli_roundtrip_and_existing_manifest_refusal(self):
        output = self.root / "assets.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(["manifest", str(self.root), "--file", "app.js", "-o", str(output)]), 0
            )
            self.assertEqual(
                main(["manifest", str(self.root), "--file", "app.js", "-o", str(output)]), 2
            )
            self.assertEqual(main(["check", str(output), "--base", self.base, "--json"]), 0)
            type(self).headers = [("Content-Type", "text/html")]
            self.assertEqual(main(["check", str(output), "--base", self.base, "--json"]), 1)

    def test_symlink_selection_refused_where_supported(self):
        try:
            os.symlink(self.root / "app.js", self.root / "link.js")
        except OSError:
            self.skipTest("symlinks unavailable for this Windows account")
        with self.assertRaises(InputError):
            make_manifest(self.root, ["link.js"])


if __name__ == "__main__":
    unittest.main()
