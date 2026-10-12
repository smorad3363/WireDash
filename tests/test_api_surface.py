#!/usr/bin/env python3
"""Compare original and proposed Flask API surfaces without importing app.

Backend implementation may evolve, but bot/public endpoint URL, HTTP methods,
function signatures, and the client API module must remain EXACTLY unchanged.
"""
import ast
import pathlib
import sys

def surface(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf8"))
    endpoints = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routed = [d for d in node.decorator_list if isinstance(d, ast.Call) and
                  isinstance(d.func, ast.Attribute) and
                  isinstance(d.func.value, ast.Name) and d.func.value.id == "app" and
                  d.func.attr in {"route", "get", "post", "put", "delete", "patch"}]
        if routed:
            endpoints[node.name] = {
                "decorators": sorted(ast.dump(d, include_attributes=False) for d in routed),
                "signature": ast.dump(node.args, include_attributes=False)
            }
    return endpoints

if __name__ == "__main__":
    old, new = surface(sys.argv[1]), surface(sys.argv[2])
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    if added or removed or changed:
        raise SystemExit("API surface changed: added="+str(added)+" removed="+str(removed)+
                         " changed="+str(changed))
    print("PASS: all", len(old), "Flask routes, methods and handler signatures unchanged")
