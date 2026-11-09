import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import unittest
from flask import Flask
from types import SimpleNamespace
from modules.PeerSpeedUI import make_blueprint

class Auth(unittest.TestCase):
    def setUp(self):
        self.app=Flask('test');self.app.secret_key='test-only'
        self.cfg=SimpleNamespace(GetConfig=lambda *_:(True,True))
        self.app.register_blueprint(make_blueprint({},self.cfg))
        self.client=self.app.test_client()
    def login(self):
        self.client.set_cookie('authToken','test-admin')
        with self.client.session_transaction() as s:
            s.update(role='admin',username='test-admin',speed_csrf='token',speed_password_login=True)
    def test_api_derived_session_denied(self):
        self.login()
        with self.client.session_transaction() as s:s['speed_password_login']=False
        self.assertEqual(self.client.get('/api/ui/speed/settings').status_code,403)
    def test_public_denied(self):
        self.assertEqual(self.client.get('/api/ui/speed/settings').status_code,403)
    def test_api_key_denied_even_with_session(self):
        self.login()
        self.assertEqual(self.client.get('/api/ui/speed/settings',headers={'wg-dashboard-apikey':'x'}).status_code,403)
    def test_auth_disabled_denied(self):
        self.login();self.cfg.GetConfig=lambda *_:(True,False)
        self.assertEqual(self.client.get('/api/ui/speed/settings').status_code,403)
    def test_client_role_denied(self):
        self.login()
        with self.client.session_transaction() as s:s['role']='client'
        self.assertEqual(self.client.get('/api/ui/speed/settings').status_code,403)
    def test_csrf_and_origin_required(self):
        self.login()
        for headers in ({},{'X-Speed-CSRF':'token'},{'X-Speed-CSRF':'token','Origin':'https://evil.invalid'}):
            self.assertEqual(self.client.post('/api/ui/speed/settings',json={},headers=headers).status_code,403)
    def test_valid_admin_reaches_handler(self):
        self.login()
        self.assertEqual(self.client.post('/api/ui/speed/settings',json={},headers={
            'X-Speed-CSRF':'token','Origin':'http://localhost'}).status_code,409)

if __name__=='__main__':unittest.main()
