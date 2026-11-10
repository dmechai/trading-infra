"""The console password must not be reachable by a process that reads keys."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from trading_infra.console import auth


@pytest.fixture
def auth_file(tmp_path):
    return tmp_path / 'console' / 'auth.json'


def test_unconfigured_is_not_an_open_door(auth_file):
    assert auth.configured(auth_file) is False
    assert auth.verify('admin', '', path=auth_file) is False
    assert auth.verify('admin', 'anything', path=auth_file) is False


def test_set_then_verify(auth_file):
    auth.set_operator('dmechai', 'a-long-enough-password', path=auth_file)
    assert auth.configured(auth_file) is True
    assert auth.operator_name(auth_file) == 'dmechai'
    assert auth.verify('dmechai', 'a-long-enough-password', path=auth_file) is True
    assert auth.verify('dmechai', 'a-long-enough-passworD', path=auth_file) is False
    assert auth.verify('someone', 'a-long-enough-password', path=auth_file) is False


def test_password_is_never_stored_in_clear(auth_file):
    auth.set_operator('dmechai', 'correct-horse-battery', path=auth_file)
    text = auth_file.read_text(encoding='utf-8')
    assert 'correct-horse-battery' not in text
    record = json.loads(text)['operator']
    assert record['algorithm'] == 'pbkdf2_sha256'
    assert record['iterations'] >= 200_000
    assert len(record['salt']) == 32


def test_short_password_refused(auth_file):
    with pytest.raises(auth.AuthError):
        auth.set_operator('dmechai', 'short', path=auth_file)
    assert auth.configured(auth_file) is False


def test_file_is_written_0600(auth_file):
    auth.set_operator('dmechai', 'a-long-enough-password', path=auth_file)
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600


def test_group_readable_file_is_refused(auth_file):
    """The secrets group may read keys; it must not read the write password."""
    auth.set_operator('dmechai', 'a-long-enough-password', path=auth_file)
    os.chmod(auth_file, 0o640)
    assert auth.configured(auth_file) is False
    assert auth.verify('dmechai', 'a-long-enough-password', path=auth_file) is False


def test_salt_differs_between_writes(auth_file):
    auth.set_operator('dmechai', 'a-long-enough-password', path=auth_file)
    first = json.loads(auth_file.read_text(encoding='utf-8'))['operator']['salt']
    auth.set_operator('dmechai', 'a-long-enough-password', path=auth_file)
    second = json.loads(auth_file.read_text(encoding='utf-8'))['operator']['salt']
    assert first != second


def test_sessions_expire_and_revoke():
    sessions = auth.Sessions(ttl=-1)
    token = sessions.issue()
    assert sessions.valid(token) is False

    sessions = auth.Sessions(ttl=3600)
    token = sessions.issue()
    assert sessions.valid(token) is True
    assert sessions.valid('not-a-token') is False
    assert sessions.valid('') is False
    sessions.revoke(token)
    assert sessions.valid(token) is False
