"""Fixed-command host backup control socket; never call shell or Docker here."""
import json
import socket

SOCKET = "/run/wgdashbackup-panel/control.sock"


def backup_panel_call(operation, **fields):
    payload = {"operation": operation, **fields}
    message = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(message) > 4096:
        return {"ok": False, "message": "Request too large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(80 if operation == "set_telegram" else
                            900 if operation in ("import_root", "import_upload") else
                            245 if operation in ("verify", "restore") else 30)
            conn.connect(SOCKET)
            conn.sendall(message + b"\n")
            with conn.makefile("rb") as stream:
                reply = stream.readline(65537)
            if not reply or len(reply) > 65536:
                raise ValueError("Malformed host response")
            result = json.loads(reply)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("Malformed host response")
            return result
    except (OSError, ValueError, json.JSONDecodeError):
        return {"ok": False, "message": "Host backup service is unavailable; rerun the WireDash installer"}
