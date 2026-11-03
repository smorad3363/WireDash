"""Detailed API logging must preserve contract values and redact credentials."""
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path("src").resolve()))
from modules.ApiBodyCapture import capture_request, capture_response, scrub, capture_query


class DictForm:
    def __init__(self, data):
        self.data = data
    def lists(self):
        return list(self.data.items())
    def to_dict(self, flat=True):
        return {k: values[0] for k, values in self.data.items()}


class Request:
    def __init__(self, body, mime="application/json"):
        self._body = body
        self.mimetype = mime
        self.is_json = mime == "application/json"
        self.content_length = len(json.dumps(body).encode()) if self.is_json else 32
        self.form = DictForm(body if isinstance(body, dict) else {})
        self.files = {}
    def get_json(self, silent=False):
        return self._body
    def get_data(self, cache=True, as_text=True):
        return json.dumps(self._body)


class Response:
    def __init__(self, body):
        self.payload = body
        self.is_json = True
    def calculate_content_length(self):
        return len(json.dumps(self.payload).encode())
    def get_json(self, silent=True):
        return self.payload


class FullCapture(unittest.TestCase):
    def test_exact_renewal_payload(self):
        raw = {"peers": ["PUBLIC_PEER_KEY"], "Job": {
            "Peer": "PUBLIC_PEER_KEY", "Configuration": "wg0",
            "Field": "quota_total_data", "Value": '{"limit_gb":"50","weight":"1"}',
            "ExpireDate": "2026-12-01 15:30:00", "Action": "restrict",
            "private_key": "SECRET_VALUE"}, "password": "password-123"}
        payload, parsed = capture_request(Request(raw))
        self.assertEqual(parsed, raw)
        self.assertEqual(payload["body"]["peers"], ["PUBLIC_PEER_KEY"])
        self.assertEqual(payload["body"]["Job"]["Value"], raw["Job"]["Value"])
        self.assertEqual(payload["body"]["Job"]["ExpireDate"], raw["Job"]["ExpireDate"])
        serialized = json.dumps(payload)
        self.assertNotIn("SECRET_VALUE", serialized)
        self.assertNotIn("password-123", serialized)
    def test_response_exact_status_and_redaction(self):
        response = capture_response(Response({
            "status": True, "message": "success",
            "data": {"JobID": "abc", "Value": "30", "PrivateKey": "SECRET"}}),
            True, "/api/savePeerScheduleJob")
        self.assertEqual(response["body"]["data"]["JobID"], "abc")
        self.assertEqual(response["body"]["data"]["Value"], "30")
        self.assertEqual(response["body"]["data"]["PrivateKey"], "[REDACTED_SECRET]")
    def test_form_and_query_multiple_values(self):
        req = Request({"peers": ["FIRST"], "quota": ["40"]}, mime="application/x-www-form-urlencoded")
        record, _ = capture_request(req)
        self.assertEqual(record["body"]["peers"], ["FIRST"])
        self.assertEqual(capture_query(DictForm({"token": ["SECRET"], "config": ["wg0"], "id": ["123"]}))["token"], "[REDACTED_SECRET]")
    def test_oversize_is_bounded(self):
        req = Request({"peers": ["test"]})
        req.content_length = 9999999
        self.assertEqual(capture_request(req)[0]["capture"], "unavailable_or_oversize")
    def test_login_response_omitted(self):
        self.assertEqual(capture_response(Response({"token": "SECRET"}), True, "/api/authenticate")["capture"],
                         "sensitive_endpoint_omitted")


if __name__ == "__main__":
    unittest.main()
