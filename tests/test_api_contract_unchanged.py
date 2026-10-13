#!/usr/bin/env python3
"""Fail closed if a PR modifies any established bot-facing HTTP API route.

Allows ONLY the internal default lookup in existing POST /api/addPeers handler.
The endpoint, required fields, other handler AST and returns stay identical.
Does not create endpoints, change request field names or response schemas.
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
assert set(a)==set(b), "Existing route handlers changed or new route added"
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
    if name=="API_UI_SystemBackupAction":
        # Adding an admin-only UI local-file deletion operation is NOT
        # permission to change any bot API or weaken backup authentication.
        baseline=b[name]
        assert ast.dump(node.body[:8], include_attributes=False) == ast.dump(
            baseline.body[:8], include_attributes=False), (
                "Backup admin/session/origin/JSON validation changed")
        new_source=ast.unparse(baseline)
        for required in ('_backup_ui_authorized()', 'Sec-Fetch-Site',
                         'bcrypt.checkpw(', 'pyotp.TOTP(', 'delete_local',
                         '"DELETE "', 'operation == "restore"',
                         'operation in ("restore", "delete_local")'):
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
print("PASS: no new/altered routes, request fields, return paths or bot handler logic")
