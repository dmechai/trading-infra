"""Trading fees both desks cost with, in one table.

Three sources, in falling order of trust:

1. the market row's own `taker_fee_rate`, when the venue publishes it per
   market -- always wins, because it is the venue's own answer;
2. the *account tier* recorded by the credential console, which signs a
   request with the configured key and stores the rate it got back;
3. the published entry tier, the worst rate the venue charges anyone.

Layer 2 exists because Binance and OKX return fee rates only to an
authenticated key, and the only process holding keys is the console. It writes
back a *number*, not a secret, so Grid and arb both cost at the real rate
without either of them reading a key. An unknown fee is never a free trade:
callers that get None withhold the net figure rather than sizing a leg.
"""
from decimal import Decimal

from .adapters._perp import number
from .credentials import _load_document, _write_document, store_path

PUBLISHED_TAKER = {
    'risex': {'mainnet': Decimal('0.0003'), 'testnet': Decimal('0.0002')},
    # The entry tier below is the worst rate either venue charges; a real
    # account can only be cheaper, so costing with it understates the edge
    # rather than inventing one. The console's probe replaces it.
    'binance': {'mainnet': Decimal('0.0005')},
    'okx': {'mainnet': Decimal('0.0005')},
}

PUBLISHED_MAKER = {
    'binance': {'mainnet': Decimal('0.0002')},
    'okx': {'mainnet': Decimal('0.0002')},
}

PUBLISHED_SOURCE = {
    'risex': 'published_t1_taker_schedule',
    'binance': 'published_vip0_schedule',
    'okx': 'published_lv1_schedule',
}

# Anything wider than this is a misread, not a discount: no venue charges a
# percent a side, and a bad probe must not make a losing pair look tradable.
MAX_PLAUSIBLE_RATE = Decimal('0.01')


def _tier_section(path=None):
    target = store_path(path)
    if not target.is_file():
        return {}
    try:
        _, document = _load_document(path)
    except Exception:
        # Costing must survive an unreadable store; it just falls back to the
        # published tier, which is never cheaper than the truth.
        return {}
    section = document.get('fee_tiers')
    return section if isinstance(section, dict) else {}


def account_tier(venue, network='mainnet', *, path=None):
    """The rate the console read with a real key, or {} if never probed."""
    entry = _tier_section(path).get(f'{venue}:{network}')
    return entry if isinstance(entry, dict) else {}


def _tier_rate(venue, network, side, path=None):
    raw = account_tier(venue, network, path=path).get(side)
    rate = number(raw) if raw is not None else None
    if rate is None or rate < 0 or rate > MAX_PLAUSIBLE_RATE:
        return None
    return Decimal(rate)


def record_account_tier(venue, network, maker, taker, *, source='api_probe', path=None):
    """Store one probed fee tier. Called by the console, never by a desk."""
    maker_rate_value, taker_rate_value = number(maker), number(taker)
    for value in (maker_rate_value, taker_rate_value):
        if value is None or value < 0 or Decimal(value) > MAX_PLAUSIBLE_RATE:
            raise ValueError(f'{venue} 返回的费率不可信：maker={maker} taker={taker}')
    target, document = _load_document(path)
    section = document.setdefault('fee_tiers', {})
    if not isinstance(section, dict):
        section = document['fee_tiers'] = {}
    import time
    section[f'{venue}:{network}'] = dict(
        venue=venue, network=network, maker=str(maker_rate_value),
        taker=str(taker_rate_value), source=source, probed_at=time.time())
    _write_document(target, document)
    return section[f'{venue}:{network}']


def taker_rate(venue, network, meta=None, *, path=None):
    """Taker fee for one venue, or None when nothing published proves it."""
    if meta is not None:
        rate = number(meta.get('taker_fee_rate'))
        if rate is not None:
            return rate
    tier = _tier_rate(venue, network, 'taker', path)
    if tier is not None:
        return tier
    value = PUBLISHED_TAKER.get(venue, {}).get(network)
    return None if value is None else Decimal(value)


def maker_rate(meta, venue=None, network='mainnet', *, path=None):
    """Published maker fee, or None so callers fall back to the taker rate."""
    rate = number(meta.get('maker_fee_rate')) if meta is not None else None
    if rate is not None:
        return rate
    tier = _tier_rate(venue, network, 'maker', path)
    if tier is not None:
        return tier
    value = PUBLISHED_MAKER.get(venue, {}).get(network)
    return None if value is None else Decimal(value)


def fee_source(venue, network, meta, rate, *, path=None):
    if rate is None:
        return 'unavailable'
    if meta is not None and number(meta.get('taker_fee_rate')) is not None:
        return meta.get('fee_source') or 'public_market'
    if _tier_rate(venue, network, 'taker', path) is not None:
        return 'account_tier'
    return PUBLISHED_SOURCE.get(venue, 'published_schedule')
