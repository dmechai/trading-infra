"""Account verification, including the mismatch that looks configured."""
import json

import pytest

from trading_infra.console import probe

# A throwaway key generated for this test only; it signs nothing real.
TEST_KEY = '0x4c0883a69102937d6231471b5dbb6204fe512961708279dcd0a7d4ea2a5b6e32'


@pytest.fixture
def store(tmp_path):
    def write(accounts):
        target = tmp_path / 'credentials.json'
        target.write_text(json.dumps({'version': 1, 'accounts': accounts}),
                          encoding='utf-8')
        target.chmod(0o600)
        return target
    return write


def test_missing_account_reports_not_configured(store):
    result = probe.check_account('risex', 'arb', path=store({}))
    assert result['ok'] is False
    assert 'risex:arb' in result['message']


def test_malformed_private_key_is_caught(store):
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': 'not-a-key',
                                  'account_address': '0x' + 'a' * 40}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is False
    assert '私钥格式无效' in result['message']


def test_zero_private_key_is_caught(store):
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': '0x' + '0' * 64,
                                  'account_address': '0x' + 'a' * 40}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is False
    assert 'secp256k1' in result['message']


def test_malformed_address_is_caught(store):
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': TEST_KEY,
                                  'account_address': 'not-an-address'}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is False
    assert '账户地址格式无效' in result['message']


def test_key_address_mismatch(store):
    """A key pasted next to the wrong address signs for the wrong account."""
    pytest.importorskip('eth_keys')
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': TEST_KEY,
                                  'account_address': '0x' + 'a' * 40}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is False
    assert '不匹配' in result['message']
    assert result['derived_address'].startswith('0x')


def test_matching_key_and_address_passes(store):
    keys = pytest.importorskip('eth_keys').keys
    derived = keys.PrivateKey(bytes.fromhex(TEST_KEY[2:])).public_key.to_checksum_address()
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': TEST_KEY,
                                  'account_address': derived}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is True
    assert result['derived_address'] == derived


def test_well_formed_key_passes_without_eth_keys(store, monkeypatch):
    """No crypto library must not mean 'this key is broken'."""
    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == 'eth_keys':
            raise ImportError('blocked for this test')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked)
    target = store({'risex:arb': {'venue': 'risex', 'label': 'arb',
                                  'auth': 'evm_key_address',
                                  'private_key': TEST_KEY,
                                  'account_address': '0x' + 'a' * 40}})
    result = probe.check_account('risex', 'arb', path=target)
    assert result['ok'] is True
    assert 'keccak' in result['message']
    assert result['derived_address'] is None


def test_fee_probe_refused_for_a_venue_without_tiers(store):
    result = probe.probe_fee_tier('risex', 'arb', path=store({}))
    assert result['ok'] is False
    assert '没有可查询的账户费率档位' in result['message']


def test_binance_client_routes_through_the_reachable_host():
    """A signed read has no public route, so every *.binance.com must move."""
    ccxt = pytest.importorskip('ccxt')
    account = {'api_key': 'k', 'secret': 's'}
    client = probe._ccxt_client('binance', account, 'swap')
    try:
        for key, value in client.urls['api'].items():
            if isinstance(value, str) and 'binance.com' in value:
                assert '//www.binance.com' in value, f'{key} still points at {value}'
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            close()
