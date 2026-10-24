"""Exercise the installed CLI against a real loopback server, including an HTML fallback."""

import gzip
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from assetwire.cli import main


def demo():
    source = Path(__file__).resolve().parent / "site"
    broken = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (source / "app.js").read_bytes()
            content_type = "text/javascript"
            if broken:
                body, content_type = b"<html>Fallback, not JavaScript</html>", "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if self.headers.get("Accept-Encoding") == "gzip":
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    def expect(arguments, expected):
        actual = main(arguments)
        if actual != expected:
            raise RuntimeError(f"Demonstration command returned {actual}; expected {expected}")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="assetwire-demo-") as directory:
                manifest = Path(directory) / "assets.json"
                base = f"http://127.0.0.1:{server.server_port}/"
                expect(["manifest", str(source), "--file", "app.js", "-o", str(manifest)], 0)
                print("Healthy deployment:")
                expect(["check", str(manifest), "--base", base], 0)
                broken = True
                print("Injected HTTP 200 HTML fallback (must fail):")
                expect(["check", str(manifest), "--base", base], 1)
                print("Demonstration passed; temporary files removed on exit.")
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    demo()
