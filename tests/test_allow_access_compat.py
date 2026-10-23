#!/usr/bin/env python3
"""Isolated regression of the authenticated legacy allow-access route.

Uses AST extraction and fake in-memory configurations; never opens a socket,
touches WireGuard interfaces, or accesses real credentials/databases.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

tree = ast.parse(Path("src/dashboard.py").read_text())
handlers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
handler = handlers["API_allowAccessPeersLegacy"]
assert len(handler.decorator_list) == 1
assert "allowAccessPeers/" in ast.unparse(handler.decorator_list[0])
assert "get_json(silent=True)" in ast.unparse(handler)
assert "API_allowAccessPeers(" in ast.unparse(handler)

class App:
    def post(self, path):
        assert path == "/api/allowAccessPeers/"
        return lambda fn: fn

class Config:
    def __init__(self, peers):
        self.peers = peers
    def getRestrictedPeersList(self):
        return [SimpleNamespace(id=k) for k in self.peers]

def response(status, message=None, status_code=200):
    return SimpleNamespace(status=status, message=message, status_code=status_code)

calls = []
state = {}
def original(name):
    calls.append(name)
    return response(True, "allowed")

class Request:
    def get_json(self, silent=False):
        assert silent
        return state["payload"]

logger = SimpleNamespace(info=lambda *a: None, exception=lambda *a: None)
globals_for_handler = dict(
    app=App(), APP_PREFIX="", ResponseObject=response, request=Request(),
    WireguardConfigurations={}, current_app=SimpleNamespace(logger=logger),
    API_allowAccessPeers=original,
)
mod = ast.Module(body=[handler], type_ignores=[])
exec(compile(ast.fix_missing_locations(mod), "<isolated legacy handler>", "exec"), globals_for_handler)
legacy = globals_for_handler["API_allowAccessPeersLegacy"]

def check(configs, payload, expected_status, expected_http, expected_calls):
    calls.clear()
    globals_for_handler["WireguardConfigurations"] = {
        k: Config(v) for k, v in configs.items()
    }
    state["payload"] = payload
    actual = legacy()
    assert (actual.status, actual.status_code, calls) == (
        expected_status, expected_http, expected_calls
    ), (actual, calls)

check({"wg0": ["key-A"]}, {"peers": ["key-A"]}, True, 200, ["wg0"])
check({"wg0": ["key-A"], "wg1": ["key-B"]}, {"peers": ["key-B"]}, True, 200, ["wg1"])
check({"wg0": ["key-A"], "wg1": ["key-A"]}, {"peers": ["key-A"]}, False, 409, [])
check({"wg0": ["key-A"]}, {"peers": ["missing"]}, False, 409, [])
check({"wg0": ["key-A"]}, {"peers": ["key-A", "key-A"]}, False, 400, [])
check({"wg0": ["key-A"]}, {"peers": []}, False, 400, [])
check({"wg0": ["key-A"]}, {"peers": "key-A"}, False, 400, [])
check({"wg0": ["key-A"]}, None, False, 400, [])
check({"wg0": ["key-A"], "wg1": ["key-B"]},
      {"peers": ["key-B"], "configurationName": "wg0"}, False, 409, [])
check({"wg0": ["key-A"], "wg1": ["key-B"]},
      {"peers": ["key-B"], "configurationName": "wg1"}, True, 200, ["wg1"])
check({"wg0": ["key-A"], "wg1": ["key-B"]},
      {"peers": ["key-A", "key-B"]}, False, 409, [])
print("PASS: legacy allow-access 405 fix resolves only uniquely restricted peers")
