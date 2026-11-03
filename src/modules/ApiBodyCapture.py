"""Safe, size-bounded full HTTP request/response capture for API diagnostics."""
import hashlib
import json
import math
import re

REDACTED = "[REDACTED_SECRET]"
MAX_BYTES = 128 * 1024
MAX_RESPONSE = 96 * 1024
MAX_GET_RESPONSE = 12 * 1024
MAX_ITEMS = 1000
MAX_DEPTH = 12
MAX_STRING = 4096

CREDENTIAL_IN_TEXT = re.compile(
    r"(?i)(?:bot)?\d{6,}:[A-Za-z0-9_-]{25,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:vmess|vless|trojan|ss|wg|wireguard)://[^\s]+"
)
KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(?:privatekey|presharedkey|password|passwd|token|apikey|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)


def secret_field(name):
    normalized = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if normalized in {"publickey", "peer", "peers", "jobid", "configurationname"}:
        return False
    secrets = ("password", "passwd", "secret", "token", "privatekey",
               "presharedkey", "authorization", "cookie", "session",
               "totp", "otp", "apikey", "accesskey", "refreshkey",
               "clientkey", "clientconfig", "certificate", "authcode",
               "rawconfig", "recovery", "seed", "webhookurl")
    return normalized == "key" or normalized in {
        "notes", "config", "link", "uri", "shareid", "invitecode"
    } or any(piece in normalized for piece in secrets)


def scrub(value, name=None, depth=0):
    """Preserve JSON field names, ordinary values and public peer keys."""
    if name is not None and secret_field(name):
        return REDACTED
    if depth > MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        text = CREDENTIAL_IN_TEXT.sub(REDACTED, value)
        text = KEY_VALUE_SECRET.sub(REDACTED, text)
        if len(text) > MAX_STRING:
            return text[:MAX_STRING] + "[TRUNCATED_STRING]"
        return text
    if isinstance(value, dict):
        out = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out["[TRUNCATED_FIELDS]"] = len(value) - index
                break
            if isinstance(key, str):
                out[key[:160]] = scrub(item, key, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        output = [scrub(item, name, depth + 1) for item in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            output.append({"[TRUNCATED_ITEMS]": len(value) - MAX_ITEMS})
        return output
    return "[UNSUPPORTED_VALUE_TYPE]"


def capture_query(args):
    return {key: scrub(values, key) for key, values in args.lists()}


def capture_request(req):
    """Return logged body and parsed body without changing the HTTP stream."""
    size = req.content_length
    payload = {"content_type": req.mimetype, "body_bytes": size}
    if size is None or size > MAX_BYTES:
        payload["capture"] = "unavailable_or_oversize"
        return payload, None
    if size == 0:
        payload.update(capture="empty", body=None)
        return payload, None
    if req.is_json:
        body = req.get_json(silent=True)
        if body is not None:
            payload.update(capture="full_json_redacted", body=scrub(body))
            return payload, body
    if req.mimetype == "application/x-www-form-urlencoded":
        payload.update(capture="full_form_redacted", body=capture_query(req.form))
        return payload, req.form.to_dict(flat=True)
    if req.mimetype.startswith("multipart/"):
        payload.update(capture="multipart_fields_only", form=capture_query(req.form),
                       files={key: {"count": len(req.files.getlist(key)),
                                    "contents": "[NOT_LOGGED]"} for key in req.files})
        return payload, req.form.to_dict(flat=True)
    if req.mimetype.startswith("text/") or req.mimetype in ("", "application/json"):
        text = req.get_data(cache=True, as_text=True)
        try:
            body = json.loads(text)
            payload.update(capture="json_unlabeled_redacted", body=scrub(body))
            return payload, body
        except (ValueError, TypeError):
            payload.update(capture="unparsed_text_digest_only",
                           sha256=hashlib.sha256(text.encode()).hexdigest())
            return payload, None
    payload["capture"] = "unsupported_binary_type"
    return payload, None


def capture_response(response, mutation, path):
    if any(part in path for part in ("/api/authenticate", "/api/newDashboardAPIKey")):
        return {"capture": "sensitive_endpoint_omitted"}
    size = response.calculate_content_length()
    limit = MAX_RESPONSE if mutation else MAX_GET_RESPONSE
    if size is None or size > limit:
        return {"capture": "oversize_or_streamed", "bytes": size}
    if response.is_json:
        return {"capture": "full_json_redacted", "bytes": size,
                "body": scrub(response.get_json(silent=True))}
    return {"capture": "non_json_not_logged", "bytes": size}
