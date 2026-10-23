#!/usr/bin/env python3
"""Mutation-free validation of the legacy bot compatibility entrypoint.

AST-extracted Flask handler with fake in-memory peers, no real services,
credentials, database writes or tunnel/network changes.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

tree = ast.parse(Path("src/dashboard.py").read_text())
handlers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
handler = handlers["API_allowAccessPeersLegacy"]
routes = [ast.unparse(d) for d in handler.decorator_list]
assert len(routes) == 2, routes
assert any("allowAccessPeers/'" in route for route in routes)
assert any("allowAccessPeers'" in route and "allowAccessPeers/'" not in route for route in routes)

class App:
    def post(self, path):
        assert path in ("/api/allowAccessPeers", "/api/allowAccessPeers/")
        return lambda fn: fn

class Config:
    def __init__(self, restricted=(), active=()):
        self.restricted = set(restricted)
        self.active = set(active)
    def getRestrictedPeersList(self):
        return [SimpleNamespace(id=p) for p in self.restricted]
    def getPeersList(self):
        return [SimpleNamespace(id=p) for p in self.active]
    def allowAccessPeers(self, keys):
        operations.append(list(keys))
        assert set(keys) <= self.restricted
        self.active.update(keys)
        self.restricted.difference_update(keys)
        return True, "Allow access successfully"

def response(status, message=None, status_code=200):
    return SimpleNamespace(status=status, message=message, status_code=status_code)

operations = []
canonical_calls = []
state = {}
def original(name):
    canonical_calls.append(name)
    return response(True, "allowed")

class Request:
    def get_json(self, silent=False):
        assert silent
        return state["payload"]

logger = SimpleNamespace(info=lambda *a: None, exception=lambda *a: None)
ns = dict(
    app=App(), APP_PREFIX="", ResponseObject=response, request=Request(),
    WireguardConfigurations={}, current_app=SimpleNamespace(logger=logger),
    API_allowAccessPeers=original,
)
exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])),
             "<isolated legacy handler>", "exec"), ns)
legacy = ns["API_allowAccessPeersLegacy"]

def check(configs, payload, expected_status, expected_http, canonical=(), changed=()):
    operations.clear()
    canonical_calls.clear()
    ns["WireguardConfigurations"] = {
        name: Config(restricted=r, active=a) for name, (r, a) in configs.items()
    }
    state["payload"] = payload
    result = legacy()
    got = (result.status, result.status_code, canonical_calls, operations)
    expected = (expected_status, expected_http, list(canonical), list(changed))
    assert got == expected, (got, expected, result.message)

check({"wg0": ({"A"}, set())}, {"peers": ["A"]}, True, 200, ["wg0"])
check({"wg0": (set(), {"A"})}, {"peers": ["A"]}, True, 200)
check({"wg0": ({"A"}, {"B"})}, {"peers": ["A", "B"]}, True, 200,
      changed=[["A"]])
check({"wg0": (set(), {"A"})}, {"peers": ["A"], "configName": ""}, True, 200)
check({"wg0": ({"A"}, set())}, {"peers": ["A"], "Configuration": "wg0"},
      True, 200, ["wg0"])
check({"wg0": ({"A"}, set()), "wg1": (set(), {"B"})},
      {"peers": ["B"]}, True, 200)
check({"wg0": ({"A"}, set()), "wg1": (set(), {"A"})},
      {"peers": ["A"]}, False, 409)
check({"wg0": ({"A"}, set())}, {"peers": ["missing"]}, False, 409)
check({"wg0": ({"A"}, set()), "wg1": (set(), {"B"})},
      {"peers": ["B"], "configurationName": "wg0"}, False, 409)
check({"wg0": ({"A"}, set()), "wg1": (set(), {"B"})},
      {"peers": ["B"], "configurationName": "wg1"}, True, 200)
check({"wg0": ({"A"}, set()), "wg1": (set(), {"B"})},
      {"peers": ["A", "B"]}, False, 409)
check({"wg0": ({"A"}, set())}, {"peers": ["A", "A"]}, False, 400)
check({"wg0": ({"A"}, set())}, {"peers": []}, False, 400)
check({"wg0": ({"A"}, set())}, {"peers": "A"}, False, 400)
check({"wg0": ({"A"}, set())}, None, False, 400)
check({"wg0": ({"A"}, set())},
      {"peers": ["A"], "configName": "wg0", "configurationName": "wg1"},
      False, 400)
check({"wg0": ({"A"}, set())},
      {"peers": ["A"], "configName": 123}, False, 400)
print("PASS: upstream API unchanged; legacy bot restricted/active/retry/ambiguous cases")
