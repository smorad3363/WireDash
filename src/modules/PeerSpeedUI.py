"""Private admin-session routes; never usable with a bot API key."""
import secrets
from urllib.parse import urlsplit
from flask import Blueprint, request, session, jsonify
from .PeerState import configuration_lock


def make_blueprint(configurations, dashboard, prefix=''):
    ui = Blueprint('speed_policy_ui', __name__, url_prefix=prefix+'/api/ui/speed')

    @ui.after_app_request
    def record_login_source(response):
        if request.path == prefix+'/api/authenticate' and request.method=='POST':
            result=response.get_json(silent=True)
            if result and result.get('status'):
                verified=False
                # Legacy authentication uses a process-global APIAccessed flag.
                # Independently verify credentials before granting this feature's
                # private session capability, including TOTP when configured.
                if not request.headers.get('wg-dashboard-apikey'):
                    credentials=request.get_json(silent=True) or {}
                    try:
                        import bcrypt,pyotp
                        verified=(isinstance(credentials.get('password'),str)
                            and credentials.get('username')==dashboard.GetConfig('Account','username')[1]
                            and bcrypt.checkpw(credentials['password'].encode(),dashboard.GetConfig('Account','password')[1].encode()))
                        if verified and dashboard.GetConfig('Account','enable_totp')[1]:
                            verified=pyotp.TOTP(dashboard.GetConfig('Account','totp_key')[1]).verify(credentials.get('totp',''))
                    except (ValueError,TypeError,AttributeError,ImportError):
                        verified=False
                session['speed_password_login'] = verified
        return response

    @ui.before_request
    def authorize():
        if (dashboard.GetConfig('Server','auth_req')[1] is not True
            or request.headers.get('wg-dashboard-apikey') is not None
            or not session.get('speed_password_login')
            or session.get('role') != 'admin' or not session.get('username')
            or session.get('username') != request.cookies.get('authToken')):
            return jsonify(status=False, message='Admin session required'), 403
        if request.method != 'GET':
            origin = request.headers.get('Origin', '')
            parsed = urlsplit(origin)
            if (parsed.scheme not in ('http','https') or parsed.netloc != request.host
                or request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or not secrets.compare_digest(str(session.get('speed_csrf','')),
                                               request.headers.get('X-Speed-CSRF',''))
                or not session.get('speed_csrf')):
                return jsonify(status=False, message='Invalid origin or CSRF token'), 403

    def store():
        for configuration in configurations.values():
            if getattr(configuration,'SpeedStore',None):
                return configuration.SpeedStore
        raise ValueError('No configuration available')

    @ui.route('/settings', methods=['GET','POST'])
    def settings():
        try:
            s=store()
            if request.method == 'POST':
                payload=request.get_json()
                s.save_defaults(payload['policy'],payload['revision'],payload['overwrite'],payload['master_enabled'])
                for c in configurations.values():
                    if getattr(c,'SpeedController',None):
                        c.SpeedController.retry_at=0
            session.setdefault('speed_csrf',secrets.token_urlsafe(32))
            with s.engine.connect() as conn:
                data=s.defaults(conn)
                data['counts']=s.counts(conn)
            return jsonify(status=True,data=data,csrf=session['speed_csrf'])
        except (ValueError,KeyError,TypeError):
            return jsonify(status=False,message='Invalid settings or stale revision; reload'),409

    @ui.route('/peer/<configuration>',methods=['GET','POST'])
    def peer(configuration):
        c=configurations.get(configuration)
        if c is None:
            return jsonify(status=False,message='Configuration not found'),404
        payload=request.args if request.method=='GET' else request.get_json(silent=True) or {}
        key=payload.get('id')
        with configuration_lock(c):
            peers=list(c.getPeersList())+list(c.getRestrictedPeersList())
            if not any(p.id==key for p in peers):
                return jsonify(status=False,message='Peer not found'),404
            try:
                if request.method=='POST':
                    c.SpeedStore.save_peer(key,payload,payload['revision'])
                    c.SpeedController.retry_at=0
                session.setdefault('speed_csrf',secrets.token_urlsafe(32))
                result=c.SpeedStore.snapshot(key)
                result['controller']={'status':c.SpeedController.status,'message':c.SpeedController.last_error}
                return jsonify(status=True,data=result,csrf=session['speed_csrf'])
            except (ValueError,KeyError,TypeError):
                return jsonify(status=False,message='Invalid policy or stale account; reload'),409
    return ui
