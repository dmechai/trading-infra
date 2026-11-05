"""Hyperliquid public perpetuals, read through the unauthenticated info API.

Separate from `hyperliquid.py`, which adapts an SDK `info` object already held
by a venue connector. This client needs no SDK and no account, so the shared
catalog can read Hyperliquid the same way it reads every other venue.
"""
from decimal import Decimal

from ..transport import PublicJSONClient
from ..venues import api_url
from ._perp import number, market_name, book
from .hyperliquid import asset_contexts


class HyperliquidPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        super().__init__(api_url('hyperliquid', network), timeout=timeout)
        self.network = network

    ZERO_ADDRESS = '0x' + '0' * 40

    def fee_schedule(self, user=None):
        """Base maker/taker rates, or an account's real tier when given its address.

        `userFees` answers for any address without a signature, so the zero
        address returns the published base tier. A real address returns the
        rates that account actually pays, including VIP tiers and the negative
        market-maker rebates.
        """
        data = self.post_json('/info', {'type': 'userFees', 'user': user or self.ZERO_ADDRESS})
        taker = number(data.get('userCrossRate'))
        maker = number(data.get('userAddRate'))
        if taker is None or maker is None:
            schedule = data.get('feeSchedule') or {}
            taker = taker if taker is not None else number(schedule.get('cross'))
            maker = maker if maker is not None else number(schedule.get('add'))
        return dict(taker_fee_rate=taker, maker_fee_rate=maker)

    def list_markets(self, user=None):
        payload = self.post_json('/info', {'type': 'metaAndAssetCtxs'})
        try:
            fees = self.fee_schedule(user)
        except Exception:
            # An unreadable schedule must leave the rate unknown, never guessed.
            fees = dict(taker_fee_rate=None, maker_fee_rate=None)
        rows = []
        for name, entry in asset_contexts(payload).items():
            try:
                rows.append(dict(self.normalize_market(name, entry['asset'], entry['ctx']), **fees))
            except (ValueError, KeyError, TypeError):
                continue
        return rows

    @staticmethod
    def normalize_market(name, asset, ctx):
        if asset.get('isDelisted'):
            raise ValueError('Hyperliquid delisted market')
        decimals = asset.get('szDecimals')
        if not isinstance(decimals, int) or not 0 <= decimals <= 8:
            raise ValueError('Hyperliquid unknown size precision')
        step = Decimal(1).scaleb(-decimals)
        bid = ask = bid_size = ask_size = None
        levels = ctx.get('impactPxs')
        if isinstance(levels, (list, tuple)) and len(levels) == 2:
            bid, ask = number(levels[0], positive=True), number(levels[1], positive=True)
            if bid is None or ask is None or bid >= ask:
                bid = ask = None
        # Hyperliquid settles funding hourly on every perpetual.
        return dict(market=market_name(name), symbol=name, market_id=name,
                    quote_currency='USD', settle_currency='USDC', contract_size=Decimal(1),
                    funding_rate=number(ctx.get('funding')), funding_interval_hours=Decimal(1),
                    next_funding_time=None,
                    mark_price=number(ctx.get('markPx'), positive=True), taker_fee_rate=None,
                    max_leverage=number(asset.get('maxLeverage'), positive=True),
                    maintenance_margin=None, initial_margin=None,
                    tick_size=None, size_step=step, min_order_size=step,
                    bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
                    source_timestamp=None,
                    # impactPxs are impact notional averages, not top of book.
                    quote_kind='impact' if bid is not None else None, reduce_only=False)

    def get_order_book(self, market, *, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError('Hyperliquid depth limit must be 1..100')
        raw = self.post_json('/info', {'type': 'l2Book', 'coin': market['symbol']})
        levels = raw.get('levels') if isinstance(raw, dict) else None
        if not isinstance(levels, (list, tuple)) or len(levels) != 2:
            raise ValueError('Hyperliquid missing depth')
        bids = [(r['px'], r['sz']) for r in levels[0][:limit]]
        asks = [(r['px'], r['sz']) for r in levels[1][:limit]]
        timestamp = number(raw.get('time'), positive=True)
        return book(bids, asks,
                    source_timestamp=float(timestamp)/1000 if timestamp else None,
                    quote_kind='orderbook')
