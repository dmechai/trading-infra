"""Operator login for the credential console, independent of every desk.

The console is the only process that may rewrite the credential store, which
makes it the highest-value target on the host. Its password therefore lives in
its own file, outside the store and outside the `dmechai-secrets` group: a desk
that can *read* every exchange key must still not be able to read the password
that lets it *write* them. Grid's session leaking must not become key-store
write access, so no credential here is shared with Grid's operator login.
"""
from pathlib import Path
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import time

DEFAULT_PATH = Path('/etc/dmechai-console/auth.json')
ENV_PATH = 'INFRA_CONSOLE_AUTH'
SESSION_TTL_SECONDS = 12 * 60 * 60
# PBKDF2 rather than a bare digest: the file is 0600, but a stolen copy should
# still not yield the password to a wordlist in minutes.
ITERATIONS = 240_000
MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    """Login is unconfigured, unreadable or unsafe. Never carries a secret."""


def auth_path(path=None):
    return Path(path or os.environ.get(ENV_PATH) or DEFAULT_PATH)


def _read(path=None):
    target = auth_path(path)
    try:
        info = target.stat()
    except OSError as exc:
        raise AuthError(f'控制台登录尚未配置：{target}') from exc
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        # Unlike the credential store, group read is *not* allowed here. The
        # secrets group exists so the desks can read keys; giving it this file
        # would hand a desk the means to rewrite them.
        raise AuthError(f'控制台登录文件必须是 0600 且属 root：{target}')
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise AuthError(f'控制台登录文件不是可读的 JSON：{target}') from exc
    if not isinstance(data, dict):
        raise AuthError(f'控制台登录文件格式错误：{target}')
    return data


def _write(data, path=None):
    target = auth_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.auth-')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def hash_password(password, *, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                 bytes.fromhex(salt), iterations)
    return dict(algorithm='pbkdf2_sha256', salt=salt,
                iterations=iterations, hash=digest.hex())


def configured(path=None):
    """Whether a usable login exists, without raising on an absent file."""
    try:
        data = _read(path)
    except AuthError:
        return False
    record = data.get('operator')
    return bool(isinstance(record, dict) and record.get('user') and record.get('hash'))


def set_operator(user, password, *, path=None):
    """Create or replace the console login. The only writer of this file."""
    user = str(user or '').strip()
    password = str(password or '')
    if not user:
        raise AuthError('操作员账号不能为空')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f'密码至少 {MIN_PASSWORD_LENGTH} 位')
    try:
        data = _read(path)
    except AuthError:
        data = {}
    record = hash_password(password)
    record['user'] = user
    record['updated_at'] = time.time()
    data['operator'] = record
    _write(data, path)


def verify(user, password, *, path=None):
    """Constant-time check of one login attempt."""
    try:
        record = (_read(path).get('operator') or {})
    except AuthError:
        return False
    stored_user = str(record.get('user') or '')
    stored_hash = str(record.get('hash') or '')
    salt = str(record.get('salt') or '')
    if not stored_user or not stored_hash or not salt:
        return False
    candidate = hash_password(str(password or ''), salt=salt,
                              iterations=int(record.get('iterations') or ITERATIONS))
    # Compare both halves unconditionally so a wrong user and a wrong password
    # take the same time and leak nothing about which one was wrong.
    user_ok = hmac.compare_digest(str(user or ''), stored_user)
    password_ok = hmac.compare_digest(candidate['hash'], stored_hash)
    return user_ok and password_ok


def operator_name(path=None):
    try:
        return str((_read(path).get('operator') or {}).get('user') or '')
    except AuthError:
        return ''


class Sessions:
    """In-memory bearer tokens. A restart logs the operator out, by design."""

    def __init__(self, ttl=SESSION_TTL_SECONDS):
        self.ttl = ttl
        self._tokens = {}

    def issue(self):
        token = secrets.token_urlsafe(32)
        self._tokens[token] = time.time() + self.ttl
        return token

    def valid(self, token):
        if not token:
            return False
        expiry = self._tokens.get(token)
        if expiry is None:
            return False
        if expiry < time.time():
            self._tokens.pop(token, None)
            return False
        return True

    def revoke(self, token):
        self._tokens.pop(token, None)

    def prune(self):
        now = time.time()
        for token in [key for key, expiry in self._tokens.items() if expiry < now]:
            self._tokens.pop(token, None)
