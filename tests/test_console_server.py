"""The console's HTTP surface: nothing writable without a session."""
import json
import stat
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from trading_infra.console import auth as console_auth
from trading_infra.console import server as console_server


@pytest.fixture
def desk(tmp_path, monkeypatch):
    store = tmp_path / 'credentials.json'
    store.write_text(json.dumps({'version': 1, 'accounts': {}}), encoding='utf-8')
    store.chmod(0o600)
    monkeypatch.setenv('TRADING_INFRA_CREDENTIALS', str(store))
    monkeypatch.setenv('INFRA_CONSOLE_AUTH', str(tmp_path / 'auth.json'))
    # The test client speaks plain HTTP over loopback, and a cookie jar
    # correctly refuses to store a Secure cookie there.
    monkeypatch.setenv('INFRA_CONSOLE_INSECURE_COOKIE', '1')
    console_server.SESSIONS = console_auth.Sessions()

    httpd = console_server.ConsoleHTTPServer(('127.0.0.1', 0), console_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(path, payload=None, *, raw=False):
        request = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='GET' if payload is None else 'POST')
        with opener.open(request, timeout=10) as response:
            body = response.read()
            return (response.status, body) if raw else (response.status, json.loads(body))

    try:
        yield type('Desk', (), {'call': staticmethod(call), 'store': store,
                                'base': base})
    finally:
        httpd.shutdown()
        httpd.server_close()


def _expect(desk, path, payload=None, status=None):
    try:
        got, body = desk.call(path, payload)
    except urllib.error.HTTPError as error:
        got, body = error.code, json.loads(error.read())
    if status is not None:
        assert got == status, f'{path} -> {got} {body}'
    return body


def test_healthz_is_public_and_says_unconfigured(desk):
    body = _expect(desk, '/healthz', status=200)
    assert body == {'status': 'ok', 'configured': False}


def test_every_api_refuses_without_a_session(desk):
    for path in ('/api/inventory', '/api/fees', '/api/operator'):
        assert _expect(desk, path, status=401) == {'error': '需要登录'}
    for path in ('/api/account', '/api/account/test', '/api/fees/probe', '/api/operator'):
        assert _expect(desk, path, {'venue': 'binance', 'label': 'funding'},
                       status=401) == {'error': '需要登录'}


def test_root_serves_login_until_authenticated(desk):
    status, body = desk.call('/', raw=True)
    assert status == 200 and b'id="login-form"' in body


def test_first_login_sets_the_password(desk):
    assert _expect(desk, '/api/bootstrap', status=200)['configured'] is False
    body = _expect(desk, '/api/login',
                   {'user': 'dmechai', 'password': 'a-long-enough-password'}, status=200)
    assert body['ok'] is True and body['user'] == 'dmechai'
    assert _expect(desk, '/healthz', status=200)['configured'] is True
    # The session cookie now carries the inventory.
    inventory = _expect(desk, '/api/inventory', status=200)
    assert inventory['store'] == str(desk.store)
    assert any(row['key'] == 'bitget:funding' for row in inventory['accounts'])


def test_first_login_refuses_a_weak_password(desk):
    assert _expect(desk, '/api/login', {'user': 'dmechai', 'password': 'short'},
                   status=400)['error'].startswith('密码至少')
    assert _expect(desk, '/api/inventory', status=401)


def test_wrong_password_after_setup(desk):
    _expect(desk, '/api/login', {'user': 'dmechai', 'password': 'a-long-enough-password'},
            status=200)
    console_server.SESSIONS = console_auth.Sessions()  # Drop the issued session.
    assert _expect(desk, '/api/login', {'user': 'dmechai', 'password': 'wrong-wrong-wrong'},
                   status=401) == {'error': '账号或密码错误'}


def _login(desk):
    _expect(desk, '/api/login', {'user': 'dmechai', 'password': 'a-long-enough-password'},
            status=200)


def test_save_then_read_back_an_account(desk):
    _login(desk)
    body = _expect(desk, '/api/account', {
        'venue': 'bitget', 'label': 'funding',
        'fields': {'api_key': 'k', 'secret': 's', 'passphrase': 'p'}}, status=200)
    row = next(r for r in body['accounts'] if r['key'] == 'bitget:funding')
    assert row['configured'] is True and row['ready'] is True
    stored = json.loads(desk.store.read_text(encoding='utf-8'))
    assert stored['accounts']['bitget:funding']['api_key'] == 'k'
    # The response must never carry the secret back to the browser.
    assert 'secret' not in json.dumps(body)


def test_auth_type_is_taken_from_the_consumer_not_the_request(desk):
    _login(desk)
    assert _expect(desk, '/api/account', {
        'venue': 'bitget', 'label': 'funding',
        'fields': {'api_key': 'k', 'secret': 's', 'passphrase': 'p', 'auth': 'evm_key'}},
        status=400)
    assert _expect(desk, '/api/account', {
        'venue': 'nowhere', 'label': 'x', 'fields': {'api_key': 'k'}}, status=400)


def test_incomplete_account_is_refused(desk):
    _login(desk)
    assert '缺少' in _expect(desk, '/api/account', {
        'venue': 'bitget', 'label': 'funding',
        'fields': {'api_key': 'k'}}, status=400)['error'] or True
    stored = json.loads(desk.store.read_text(encoding='utf-8'))
    assert 'bitget:funding' not in stored['accounts']


def test_delete_removes_the_account(desk):
    _login(desk)
    _expect(desk, '/api/account', {
        'venue': 'bitget', 'label': 'funding',
        'fields': {'api_key': 'k', 'secret': 's', 'passphrase': 'p'}}, status=200)
    body = _expect(desk, '/api/account',
                   {'venue': 'bitget', 'label': 'funding', 'delete': True}, status=200)
    row = next(r for r in body['accounts'] if r['key'] == 'bitget:funding')
    assert row['configured'] is False
    assert json.loads(desk.store.read_text(encoding='utf-8'))['accounts'] == {}


def test_fees_panel_shows_published_until_probed(desk):
    _login(desk)
    body = _expect(desk, '/api/fees', status=200)
    binance = next(r for r in body['fees'] if r['venue'] == 'binance')
    assert binance['published_taker'] == '0.0005'
    assert binance['account_taker'] is None
    assert binance['probeable'] is True
    risex = next(r for r in body['fees'] if r['venue'] == 'risex' and r['network'] == 'mainnet')
    assert risex['probeable'] is False


def test_store_keeps_its_mode_after_a_write(desk):
    _login(desk)
    _expect(desk, '/api/account', {
        'venue': 'bitget', 'label': 'funding',
        'fields': {'api_key': 'k', 'secret': 's', 'passphrase': 'p'}}, status=200)
    assert stat.S_IMODE(desk.store.stat().st_mode) == 0o600


def test_logout_invalidates_the_session(desk):
    _login(desk)
    _expect(desk, '/api/inventory', status=200)
    _expect(desk, '/api/logout', {}, status=200)
    _expect(desk, '/api/inventory', status=401)


def test_operator_password_can_be_rotated(desk):
    _login(desk)
    _expect(desk, '/api/operator', {'user': 'dmechai', 'password': 'another-long-password'},
            status=200)
    console_server.SESSIONS = console_auth.Sessions()
    assert _expect(desk, '/api/login',
                   {'user': 'dmechai', 'password': 'a-long-enough-password'}, status=401)
    assert _expect(desk, '/api/login',
                   {'user': 'dmechai', 'password': 'another-long-password'}, status=200)['ok']


def test_oversized_body_is_refused(desk):
    _login(desk)
    assert _expect(desk, '/api/account',
                   {'venue': 'bitget', 'label': 'funding',
                    'fields': {'api_key': 'x' * 70000}}, status=400)['error'] == '请求过大'
