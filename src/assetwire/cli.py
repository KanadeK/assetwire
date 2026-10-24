import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import InputError, check, load_manifest, make_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check deployed static files against selected build bytes."
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("manifest")
    build.add_argument("root", type=Path)
    build.add_argument("--file", action="append", required=True, dest="files")
    build.add_argument(
        "--mime", action="append", default=[], help="override MIME as relative/path=type/subtype"
    )
    build.add_argument("-o", "--output", type=Path, required=True)
    probe = sub.add_parser("check")
    probe.add_argument("manifest", type=Path)
    probe.add_argument("--base", required=True)
    probe.add_argument("--encoding", action="append", choices=["identity", "gzip"])
    probe.add_argument("--timeout", type=float, default=10.0)
    probe.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            overrides = {}
            for value in args.mime:
                if "=" not in value:
                    raise InputError("--mime must use path=type/subtype")
                path, mime = value.rsplit("=", 1)
                if path in overrides:
                    raise InputError("duplicate MIME override")
                overrides[path] = mime
            data = make_manifest(args.root, args.files, overrides)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
            print(f"Recorded {len(data['files'])} selected assets")
            return 0
        data = load_manifest(args.manifest)
        report = check(data, args.base, tuple(args.encoding or ["identity", "gzip"]), args.timeout)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        else:
            print("PASS" if report["ok"] else "FAIL")
            for item in report["checks"]:
                if item["issues"]:
                    print(
                        f"{json.dumps(item['path'])} [{item['requested_encoding']}]: {', '.join(item['issues'])}"
                    )
        return 0 if report["ok"] else 1
    except (InputError, OSError, UnicodeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        else:
            print(f"assetwire: {str(exc)!r}", file=sys.stderr)
        return 2
