#!/usr/bin/env python3
"""Real Flask JSON and disposable SQLite regression; no VPN interfaces."""
import ast
import pathlib
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from flask import Flask
from flask.json.provider import DefaultJSONProvider
from modules.PeerJobs import PeerJobs
from modules.PeerJob import PeerJob
from modules.ConfigurationReadModel import configuration_info_payload
from benchmark_synthetic_api import make_fake_config, Provider

with tempfile.TemporaryDirectory() as tmp:
    cfg = make_fake_config(4, tmp)
    configurations = {cfg.Name: cfg}
    logger = MagicMock()
    logger.getFailingJobs.return_value = []
    settings = SimpleNamespace(GetConfig=lambda *_: (True, 'sqlite'))
    with patch('modules.PeerJobs.ConnectionString', return_value='sqlite:///'+tmp+'/jobs.db'), patch('modules.PeerJobs.PeerJobLogger', return_value=logger):
        jobs = PeerJobs(settings, configurations, cfg.AllPeerShareLinks)
    cfg.AllPeerJobs = jobs
    peer = cfg.Peers[0]
    job = PeerJob('test-date', cfg.Name, peer.id, 'date', 'lgt', '2099-01-01 00:00:00', '', '', 'restrict')
    ok, result = jobs.saveJob(job)
    assert ok, result
    job.Value = '2099-02-01 00:00:00'
    assert jobs.saveJob(job)[0]
    assert jobs.saveJob(job)[0]
    assert len(jobs.Jobs) == 1 and peer.jobs[0].Value == job.Value

    app = Flask('legacy-runtime-test')
    app.json = Provider(app)
    def response(status=True, message=None, data=None, status_code=200):
        return app.make_response((dict(status=status, message=message, data=data), status_code))
    source = ast.parse(pathlib.Path('src/dashboard.py').read_text())
    wanted = {'API_getConfigurationInfo', 'API_savePeerScheduleJob'}
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    from flask import request
    ns = dict(app=app, APP_PREFIX='', request=request, ResponseObject=response,
              WireguardConfigurations=configurations, AllPeerJobs=jobs, PeerJob=PeerJob,
              configuration_info_payload=configuration_info_payload)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<real handlers>', 'exec'), ns)
    client = app.test_client()
    r = client.get('/api/getWireguardConfigurationInfo', query_string={'configurationName':cfg.Name})
    assert r.status_code == 200 and r.is_json
    data = r.get_json()['data']
    assert data['configurationPeers'][0]['configuration'] == data['configurationInfo']
    assert data['configurationPeers'][0]['jobs'][0]['Value'] == job.Value
    assert client.get('/api/getWireguardConfigurationInfo').get_json()['status'] is False
    invalid = client.post('/api/savePeerScheduleJob', json={'Job':{'Configuration':None, 'Peer':peer.id}})
    assert invalid.status_code == 200 and invalid.get_json()['message'] == 'Configuration does not exist'
    assert jobs.deleteJob(job)[0]
    assert peer.jobs == []
    # Expired IDs are not silently revived; new IDs retain previous history.
    with patch('traceback.print_exc'):
        assert jobs.saveJob(job)[0] is False
    job.JobID = 'new-date'
    assert jobs.saveJob(job)[0]
    logger.getFailingJobs.return_value = [{'JobID':job.JobID}]
    jobs.cleanJob()
    jobs._PeerJobs__getJobs()
    assert jobs.Jobs == [] and peer.jobs == []
    assert len(jobs.getAllJobs(cfg.Name)) == 2
print('PASS: real Flask JSON, SQLite create/update/retry/delete/cleanup; expired ID behavior preserved')
