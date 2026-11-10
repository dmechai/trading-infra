"""The credential console: the only process that may write the shared store.

Grid and arb both mount the store read-only. Neither can create, change or
delete an account, which means a session stolen from a trading desk cannot be
turned into key-store write access. This service holds the write mount and its
own login, and it is the only place an account is filled in.

It serves a small static page and a JSON API; no framework, no template
engine, and nothing imported at module scope that performs I/O.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse
import json
import os
import traceback

from ..credentials import (CONSUMERS, CredentialError, REQUIRED, delete_account,
                           inventory, save_account, store_path)
from ..fees import PUBLISHED_MAKER, PUBLISHED_TAKER, account_tier
from . import auth as console_auth
from .probe import FEE_PROBES, check_account, probe_fee_tier

STATIC_DIR = Path(__file__).resolve().parent / 'static'
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/login.html': ('login.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
}
PUBLIC_PATHS = {'/login.html', '/styles.css', '/healthz', '/api/login', '/api/bootstrap'}
MAX_BODY_BYTES = 64 * 1024


def _cookie_flags():
    """Session cookie attributes. `Secure` is on unless explicitly disabled.

    Deliberately not derived from `X-Forwarded-Proto`: a request header can be
    stripped, and losing `Secure` would be silent. Only an operator setting an
    environment variable can drop it, which is what the tests do over loopback.
    """
    secure = os.environ.get('INFRA_CONSOLE_INSECURE_COOKIE', '') != '1'
    return 'HttpOnly; SameSite=Strict; ' + ('Secure; ' if secure else '')

SESSIONS = console_auth.Sessions()


def _venue_networks():
    """Which (venue, network) pairs can carry a fee tier."""
    pairs = set()
    for table in (PUBLISHED_TAKER, PUBLISHED_MAKER):
        for venue, networks in table.items():
            for network in networks:
                pairs.add((venue, network))
    return sorted(pairs)


def fee_overview(path=None):
    """Published entry tier next to the probed account tier, for every venue."""
    rows = []
    for venue, network in _venue_networks():
        tier = account_tier(venue, network, path=path)
        published_taker = PUBLISHED_TAKER.get(venue, {}).get(network)
        published_maker = PUBLISHED_MAKER.get(venue, {}).get(network)
        label = next((spec['label'] for spec in CONSUMERS if spec['venue'] == venue), None)
        rows.append(dict(
            venue=venue, network=network, label=label,
            probeable=venue in FEE_PROBES,
            published_taker=None if published_taker is None else str(published_taker),
            published_maker=None if published_maker is None else str(published_maker),
            account_taker=tier.get('taker'), account_maker=tier.get('maker'),
            probed_at=tier.get('probed_at'), source=tier.get('source')))
    return dict(store=str(store_path(path)), fees=rows)


class ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    block_on_close = False


class Handler(BaseHTTPRequestHandler):
    server_version = 'infra-console'
    sys_version = ''

    # --- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):
        # Default logging prints the full request line; a query string here
        # would be written to disk, so only the method and path are kept.
        parsed = urlparse(self.path)
        self.log_date_time_string()
        print(f'{self.address_string()} {self.command} {parsed.path}', flush=True)

    def _json(self, status, payload, headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-store')
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        name, content_type = STATIC_FILES[path]
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            self._json(404, {'error': '页面缺失'})
            return
        self.send_response(200)
        self.send_header('content-type', content_type)
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-store')
        self.send_header('x-content-type-options', 'nosniff')
        self.send_header('x-frame-options', 'DENY')
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        cookie = SimpleCookie(self.headers.get('cookie') or '')
        item = cookie.get('infra_session')
        return item.value if item else ''

    def _authenticated(self):
        return SESSIONS.valid(self._token())

    def _body(self):
        try:
            length = int(self.headers.get('content-length') or 0)
        except ValueError:
            raise ValueError('请求长度无效')
        if length > MAX_BODY_BYTES:
            raise ValueError('请求过大')
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('请求不是合法 JSON')
        if not isinstance(payload, dict):
            raise ValueError('请求必须是 JSON 对象')
        return payload

    # --- routes ---------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/healthz':
            self._json(200, {'status': 'ok', 'configured': console_auth.configured()})
            return
        if path == '/favicon.ico':
            self.send_response(204)
            self.send_header('content-length', '0')
            self.end_headers()
            return
        if path in {'/', '/index.html'}:
            self._static('/index.html' if self._authenticated() else '/login.html')
            return
        if path in STATIC_FILES and path in PUBLIC_PATHS:
            self._static(path)
            return
        if path in STATIC_FILES:
            if not self._authenticated():
                self._json(401, {'error': '需要登录'})
                return
            self._static(path)
            return
        if not path.startswith('/api/'):
            self._json(404, {'error': '页面不存在'})
            return
        if path == '/api/bootstrap':
            # Lets the login page say "set a password" instead of "wrong
            # password" on a host where the console has never been configured.
            self._json(200, {'configured': console_auth.configured(),
                             'user': console_auth.operator_name()})
            return
        if not self._authenticated():
            self._json(401, {'error': '需要登录'})
            return
        try:
            if path == '/api/inventory':
                self._json(200, inventory())
                return
            if path == '/api/fees':
                self._json(200, fee_overview())
                return
            if path == '/api/operator':
                self._json(200, {'user': console_auth.operator_name(),
                                 'store': str(store_path()),
                                 'auth_file': str(console_auth.auth_path())})
                return
        except Exception as exc:
            self._fail(exc)
            return
        self._json(404, {'error': '接口不存在'})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._body()
        except ValueError as exc:
            self._json(400, {'error': str(exc)})
            return
        if path == '/api/login':
            user = str(payload.get('user') or '')
            password = str(payload.get('password') or '')
            if not console_auth.configured():
                # First run: the first POST sets the login rather than leaving
                # a writable key store behind an empty password.
                try:
                    console_auth.set_operator(user, password)
                except console_auth.AuthError as exc:
                    self._json(400, {'error': str(exc)})
                    return
            elif not console_auth.verify(user, password):
                self._json(401, {'error': '账号或密码错误'})
                return
            token = SESSIONS.issue()
            self._json(200, {'ok': True, 'user': console_auth.operator_name()}, headers=[
                ('set-cookie',
                 f'infra_session={token}; Path=/; {_cookie_flags()}'
                 f'Max-Age={console_auth.SESSION_TTL_SECONDS}')])
            return
        if not self._authenticated():
            self._json(401, {'error': '需要登录'})
            return
        if path == '/api/logout':
            SESSIONS.revoke(self._token())
            self._json(200, {'ok': True}, headers=[
                ('set-cookie', f'infra_session=; Path=/; {_cookie_flags()}Max-Age=0')])
            return
        try:
            if path == '/api/account':
                self._json(200, self._save_account(payload))
                return
            if path == '/api/account/test':
                venue, label = self._target(payload)
                self._json(200, check_account(venue, label))
                return
            if path == '/api/fees/probe':
                venue, label = self._target(payload)
                network = str(payload.get('network') or 'mainnet')
                result = probe_fee_tier(venue, label, network)
                self._json(200, {**result, **fee_overview()})
                return
            if path == '/api/operator':
                console_auth.set_operator(payload.get('user'), payload.get('password'))
                self._json(200, {'ok': True, 'user': console_auth.operator_name()})
                return
        except (ValueError, CredentialError, console_auth.AuthError) as exc:
            self._json(400, {'error': str(exc)})
            return
        except Exception as exc:
            self._fail(exc)
            return
        self._json(404, {'error': '接口不存在'})

    # --- handlers -------------------------------------------------------
    @staticmethod
    def _target(payload):
        venue = str((payload or {}).get('venue') or '').strip()
        label = str((payload or {}).get('label') or '').strip()
        if not venue or not label:
            raise ValueError('缺少场所或账户标签')
        return venue, label

    def _save_account(self, payload):
        venue, label = self._target(payload)
        if payload.get('delete'):
            delete_account(venue, label)
            return inventory()
        fields = payload.get('fields')
        if not isinstance(fields, dict):
            raise ValueError('缺少字段')
        auth = str(fields.get('auth') or '').strip()
        if not auth:
            # The form knows the auth type from the inventory row; filling it
            # in here keeps a hand-made request from inventing one.
            auth = next((spec['auth'] for spec in CONSUMERS
                         if spec['venue'] == venue and spec['label'] == label), '')
        if auth not in REQUIRED:
            raise ValueError(f'未知的鉴权类型：{auth or "(空)"}')
        save_account(venue, label, {**fields, 'auth': auth})
        return inventory()

    def _fail(self, exc):
        # Never return a trace to the browser: it can quote a line of the
        # store. It goes to the container log, where only root reads it.
        traceback.print_exc()
        self._json(500, {'error': f'{type(exc).__name__}: {exc}'})


def serve(host=None, port=None):
    host = host or os.environ.get('INFRA_CONSOLE_HOST', '127.0.0.1')
    port = int(port or os.environ.get('INFRA_CONSOLE_PORT', '8772'))
    server = ConsoleHTTPServer((host, port), Handler)
    print(f'credential console on http://{host}:{port} '
          f'(store {store_path()}, auth {console_auth.auth_path()})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    serve()
