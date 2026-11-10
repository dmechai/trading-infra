"""Fee resolution order, and the guard against a bad probe."""
import json
import stat
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from trading_infra import fees


@pytest.fixture
def store(tmp_path):
    target = tmp_path / 'credentials.json'
    target.write_text(json.dumps({'version': 1, 'accounts': {}}), encoding='utf-8')
    target.chmod(0o600)
    return target


def test_published_tier_used_when_nothing_probed(store):
    assert fees.taker_rate('binance', 'mainnet', path=store) == Decimal('0.0005')
    assert fees.maker_rate(None, 'binance', 'mainnet', path=store) == Decimal('0.0002')
    assert fees.fee_source('binance', 'mainnet', None,
                           Decimal('0.0005'), path=store) == 'published_vip0_schedule'


def test_market_row_beats_everything(store):
    fees.record_account_tier('binance', 'mainnet', '0.0001', '0.0003', path=store)
    meta = {'taker_fee_rate': '0.00018'}
    assert fees.taker_rate('binance', 'mainnet', meta, path=store) == Decimal('0.00018')
    assert fees.fee_source('binance', 'mainnet', meta,
                           Decimal('0.00018'), path=store) == 'public_market'


def test_account_tier_beats_published(store):
    fees.record_account_tier('binance', 'mainnet', '0.0001', '0.0003', path=store)
    assert fees.taker_rate('binance', 'mainnet', path=store) == Decimal('0.0003')
    assert fees.maker_rate(None, 'binance', 'mainnet', path=store) == Decimal('0.0001')
    assert fees.fee_source('binance', 'mainnet', None,
                           Decimal('0.0003'), path=store) == 'account_tier'


def test_unknown_venue_has_no_rate(store):
    assert fees.taker_rate('nowhere', 'mainnet', path=store) is None
    assert fees.fee_source('nowhere', 'mainnet', None, None, path=store) == 'unavailable'


def test_tier_is_stored_without_touching_accounts(store):
    fees.record_account_tier('okx', 'mainnet', '0.0002', '0.0004', path=store)
    document = json.loads(store.read_text(encoding='utf-8'))
    assert document['accounts'] == {}
    entry = document['fee_tiers']['okx:mainnet']
    assert entry['maker'] == '0.0002' and entry['taker'] == '0.0004'
    assert entry['probed_at'] > 0


def test_implausible_probe_is_refused(store):
    """A misread rate must not make a losing pair look tradable."""
    with pytest.raises(ValueError):
        fees.record_account_tier('binance', 'mainnet', '0.0002', '0.5', path=store)
    with pytest.raises(ValueError):
        fees.record_account_tier('binance', 'mainnet', '-0.1', '0.0003', path=store)
    with pytest.raises(ValueError):
        fees.record_account_tier('binance', 'mainnet', None, '0.0003', path=store)
    assert fees.taker_rate('binance', 'mainnet', path=store) == Decimal('0.0005')


def test_corrupt_tier_falls_back_to_published(store):
    document = json.loads(store.read_text(encoding='utf-8'))
    document['fee_tiers'] = {'binance:mainnet': {'maker': 'nonsense', 'taker': '9'}}
    store.write_text(json.dumps(document), encoding='utf-8')
    store.chmod(0o600)
    # 9 is far outside the plausible band, so it is ignored rather than used.
    assert fees.taker_rate('binance', 'mainnet', path=store) == Decimal('0.0005')
    assert fees.maker_rate(None, 'binance', 'mainnet', path=store) == Decimal('0.0002')


def test_unreadable_store_still_costs(tmp_path):
    """Costing must survive a store it cannot read, never return a free trade."""
    missing = tmp_path / 'absent.json'
    assert fees.taker_rate('binance', 'mainnet', path=missing) == Decimal('0.0005')
    bad = tmp_path / 'wide-open.json'
    bad.write_text('{"accounts":{}}', encoding='utf-8')
    bad.chmod(0o666)
    assert fees.taker_rate('binance', 'mainnet', path=bad) == Decimal('0.0005')
