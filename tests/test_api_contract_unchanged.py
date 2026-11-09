#!/usr/bin/env python3
"""Fail closed if a PR modifies any established bot-facing HTTP API route.

Allows ONLY the internal default lookup in existing POST /api/addPeers handler.
The endpoint, required fields, other handler AST and returns stay identical.
Permits ONLY reviewed changes to the isolated legacy allow-access compatibility alias.
Existing bot endpoints, request fields and response schemas remain unchanged.
"""
import ast
import pathlib
import subprocess
import sys

base = sys.argv[1] if len(sys.argv)>1 else "FETCH_HEAD"
old = subprocess.check_output(["git","show",f"{base}:src/dashboard.py"],text=True)
new = pathlib.Path("src/dashboard.py").read_text()
def endpoints(src):
    out={}
    for node in ast.parse(src).body:
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        route=False
        for d in node.decorator_list:
            if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute):
                if d.func.attr in {"route","get","post","put","delete","patch"}:
                    route=True
        if route:
            out[node.name]=node
    return out
a,b=endpoints(old),endpoints(new)
assert not (set(a)-set(b)), "Existing route handler removed"
assert set(b)-set(a) <= {"API_allowAccessPeersLegacy"}, "Unapproved new API route"
class NormalizeDefault(ast.NodeTransformer):
    def visit_Call(self,node):
        self.generic_visit(node)
        if (isinstance(node.func,ast.Name) and node.func.id=="parse_creation_limits"
                and len(node.args)==2
                and isinstance(node.args[1],ast.Subscript)):
            # Only existing internal fallback retrieval is allowed here.
            get=node.args[1].value
            assert isinstance(get,ast.Call) and getattr(get.func,"attr",None)=="GetConfig"
            assert len(get.args)==2 and all(isinstance(x,ast.Constant) for x in get.args)
            assert [x.value for x in get.args]==["Peers","peer_traffic_factor"]
            node.args.pop()
        return node
for name,node in a.items():
    # Reviewed consistency fix: only the listed bot operations gain a lock.
    # The URL decorators, arguments and original validation remain identical.
    candidate = b[name]
    for route in (node, candidate):
        locks = [d for d in route.decorator_list if isinstance(d, ast.Call)
                 and isinstance(d.func, ast.Name) and d.func.id == "locked_configuration_route"]
        if locks:
            assert name in {"API_addPeers", "API_resetPeerData", "API_updatePeerSettings"}
            assert len(locks) == 1 and ast.unparse(locks[0]) == "locked_configuration_route(WireguardConfigurations)"
            route.decorator_list.remove(locks[0])
    if name == "API_resetPeerData":
        assert ast.dump(node.args) == ast.dump(candidate.args)
        assert [ast.dump(d) for d in node.decorator_list] == [ast.dump(d) for d in candidate.decorator_list]
        assert [ast.dump(x) for x in node.body[:7]] == [ast.dump(x) for x in candidate.body[:7]]
        expected = ast.parse("resetStatus, message = wgc.resetPeerData(id, type)\nreturn ResponseObject(status=resetStatus, message=message if not resetStatus else None)")
        assert [ast.dump(x) for x in candidate.body[7:]] == [ast.dump(x) for x in expected.body]
        continue
    if name == "API_UI_getConfigurationPage":
        # UI-only sort extension; the bot-facing configuration route is still
        # compared without exceptions. Paging/auth/validation stay intact.
        class NormalizeUISort(ast.NodeTransformer):
            def visit_Constant(self, value):
                if value.value == "created_desc":
                    value.value = "name"
                return value
            def visit_Tuple(self, value):
                self.generic_visit(value)
                if all(isinstance(x, ast.Constant) for x in value.elts):
                    values = [x.value for x in value.elts]
                    if values == ["name", "name", "status", "allowed_ip", "restricted"]:
                        value.elts = value.elts[1:]
                return value
        assert ast.dump(NormalizeUISort().visit(node), include_attributes=False) == ast.dump(
            NormalizeUISort().visit(candidate), include_attributes=False), "UI page changes beyond newest sort"
        continue
    if name=="API_allowAccessPeersLegacy":
        # Reviewed legacy compatibility shim only. The original upstream
        # /api/allowAccessPeers/<configName> remains AST-identical and all
        # alias behavioral cases are checked in test_allow_access_compat.py.
        assert "API_allowAccessPeers(name)" in ast.unparse(b[name])
        assert "request.get_json(silent=True)" in ast.unparse(b[name])
        continue
    if name=="API_UI_SystemBackupAction":
        # Adding an admin-only UI local-file deletion operation is NOT
        # permission to change any bot API or weaken backup authentication.
        baseline=b[name]
        assert [ast.dump(stmt, include_attributes=False) for stmt in node.body[:8]] == [
            ast.dump(stmt, include_attributes=False) for stmt in baseline.body[:8]], (
                "Backup admin/session/origin/JSON validation changed")
        new_source=ast.unparse(baseline)
        for required in ('_backup_ui_authorized()', 'Sec-Fetch-Site',
                         'bcrypt.checkpw(', 'pyotp.TOTP(', 'delete_local',
                         'DELETE ', 'operation ==', 'operation in'):
            assert required in new_source, (
                "Backup delete operation weakened/omitted: "+required)
        continue

    # The prior release already includes the optional server-wide default
    # argument; normalize BOTH baseline and changed route for later PRs.
    # Otherwise an unchanged API_addPeers fails after the first merge.
    lhs=ast.dump(NormalizeDefault().visit(node) if name=="API_addPeers" else node,
                 include_attributes=False)
    rhs=ast.dump(NormalizeDefault().visit(b[name]) if name=="API_addPeers" else b[name],
                 include_attributes=False)
    assert lhs==rhs, "Existing API route modified: "+name
print("PASS: bot URLs/fields preserved; reviewed reset failure propagation and operation locks permitted")
