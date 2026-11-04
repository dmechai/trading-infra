"""Validation shared by read-only perpetual market adapters."""
from decimal import Decimal, InvalidOperation
import re


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and (not positive or result > 0) else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def market_name(base):
    if not isinstance(base, str) or not re.fullmatch(r'[A-Za-z0-9_]+', base):
        raise ValueError('Invalid perpetual base')
    return base.upper() + '-USD'


def book(bids, asks, **metadata):
    bids = [(number(p, positive=True), number(q, positive=True)) for p, q in bids]
    asks = [(number(p, positive=True), number(q, positive=True)) for p, q in asks]
    if not bids or not asks or any(p is None or q is None for p, q in bids + asks):
        raise ValueError('Missing or invalid book levels')
    bids.sort(reverse=True)
    asks.sort()
    if bids[0][0] >= asks[0][0]:
        raise ValueError('Crossed book')
    return dict(bid=bids[0][0], ask=asks[0][0], bid_size=bids[0][1],
                ask_size=asks[0][1], bids=bids, asks=asks, **metadata)
