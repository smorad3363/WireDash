#!/usr/bin/env python3
"""Assert getLink does not force a complete link-table DB fetch per peer."""
import ast
import pathlib
import sys


def main():
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "src/modules/PeerShareLinks.py")
    root = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = next((n for n in root.body if isinstance(n, ast.ClassDef)
                and n.name == "PeerShareLinks"), None)
    if cls is None:
        raise SystemExit("FAIL: PeerShareLinks class not found")
    method = next((n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "getLink"), None)
    if method is None:
        raise SystemExit("FAIL: getLink method not found")
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "__getSharedLinks":
            raise SystemExit("FAIL: getLink() still reloads full shared-links table")
    print("PASS: getLink() does not call __getSharedLinks")


if __name__ == "__main__":
    main()
