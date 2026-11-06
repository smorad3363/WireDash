import configparser
import os
from datetime import datetime
global sqldb, cursor, DashboardConfig, WireguardConfigurations, AllPeerJobs, JobLogger, Dash

# Do not import dashboard in the Gunicorn master before forking workers:
# dashboard initializes SQLite connections, and inherited WAL handles can
# cause stale database reads and "database disk image is malformed" errors.
ini_config = configparser.RawConfigParser()
ini_config.read(['/data/wg-dashboard.ini', './wg-dashboard.ini'])
app_host = ini_config.get('Server', 'app_ip', fallback='0.0.0.0').strip() or '0.0.0.0'
app_port = ini_config.getint('Server', 'app_port', fallback=10086)
date = datetime.today().strftime('%Y_%m_%d_%H_%M_%S')

def post_worker_init(worker):
    import dashboard
    dashboard.startThreads()
    dashboard.DashboardPlugins.startThreads()

worker_class = 'gthread'
workers = 1
threads = 2
bind = f"{app_host}:{app_port}"
daemon = True
pidfile = './gunicorn.pid'
wsgi_app = "dashboard:app"
accesslog = f"./log/access_{date}.log"
loglevel = os.environ['log_level'] if 'log_level' in os.environ else 'info'
capture_output = True
errorlog = f"./log/error_{date}.log"
pythonpath = "., ./modules"

print(f"[Gunicorn] WGDashboard w/ Gunicorn will be running on {bind}", flush=True)
print(f"[Gunicorn] Access log file is at {accesslog}", flush=True)
print(f"[Gunicorn] Error log file is at {errorlog}", flush=True)
