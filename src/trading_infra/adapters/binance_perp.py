"""Binance USDⓈ-M public perpetuals. Public market routes only, no account."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from ..transport import PublicJSONClient
from ..venues import BINANCE_PUBLIC_ROUTES
from ._perp import number, market_name, book


class BinancePerpPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        if network != 'mainnet':
            raise ValueError('Binance public perpetuals are mainnet only')
        super().__init__(BINANCE_PUBLIC_ROUTES['fapiPublic'], timeout=timeout)
        self.network = network

    def list_markets(self):
        requests = [('info', '/exchangeInfo', {}), ('books', '/ticker/bookTicker', {}),
                    ('premium', '/premiumIndex', {})]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {n: pool.submit(self.get_json, p, **q) for n, p, q in requests}
            data = {n: f.result() for n, f in futures.items()}
        books = {r['symbol']: r for r in data['books']}
        premium = {r['symbol']: r for r in data['premium']}
        rows = []
        for row in (data['info'].get('symbols') or []):
            if (row.get('status') != 'TRADING' or row.get('contractType') != 'PERPETUAL'
                    or row.get('quoteAsset') != 'USDT' or row.get('marginAsset') != 'USDT'):
                continue
            try:
                key = row['symbol']
                rows.append(self.normalize_market(row, books.get(key, {}), premium.get(key, {})))
            except (ValueError, KeyError, TypeError):
                continue
        return rows

    @staticmethod
    def normalize_market(row, ticker, premium):
        filters = {f.get('filterType'): f for f in (row.get('filters') or [])}
        bid = number(ticker.get('bidPrice'), positive=True)
        ask = number(ticker.get('askPrice'), positive=True)
        if bid is None or ask is None or bid >= ask:
            bid = ask = None
        timestamp = number(ticker.get('time'), positive=True)
        # Binance publishes the next settlement; the interval itself is per-symbol.
        hours = number(row.get('fundingIntervalHours'), positive=True)
        nxt = number(premium.get('nextFundingTime'), positive=True)
        return dict(market=market_name(row['baseAsset']), symbol=row['symbol'], market_id=row['symbol'],
                    quote_currency='USDT', settle_currency='USDT', contract_size=Decimal(1),
                    funding_rate=number(premium.get('lastFundingRate')),
                    funding_interval_hours=hours if hours else Decimal(8),
                    next_funding_time=int(nxt/1000) if nxt else None,
                    mark_price=number(premium.get('markPrice'), positive=True), taker_fee_rate=None,
                    max_leverage=None, maintenance_margin=None, initial_margin=None,
                    tick_size=number((filters.get('PRICE_FILTER') or {}).get('tickSize'), positive=True),
                    size_step=number((filters.get('LOT_SIZE') or {}).get('stepSize'), positive=True),
                    min_order_size=number((filters.get('LOT_SIZE') or {}).get('minQty'), positive=True),
                    bid=bid, ask=ask,
                    bid_size=number(ticker.get('bidQty'), positive=True) if bid is not None else None,
                    ask_size=number(ticker.get('askQty'), positive=True) if ask is not None else None,
                    source_timestamp=float(timestamp)/1000 if timestamp else None,
                    quote_kind='orderbook', reduce_only=False)

    def get_order_book(self, market, *, limit=20):
        allowed = (5, 10, 20, 50, 100, 500, 1000)
        depth = next((n for n in allowed if n >= limit), None)
        if depth is None:
            raise ValueError('Binance depth limit must be <= 1000')
        raw = self.get_json('/depth', symbol=market['symbol'], limit=depth)
        if not raw.get('bids') or not raw.get('asks'):
            raise ValueError('Binance missing depth')
        timestamp = number(raw.get('T') or raw.get('E'), positive=True)
        return book([(p, q) for p, q in raw['bids'][:limit]],
                    [(p, q) for p, q in raw['asks'][:limit]],
                    source_timestamp=float(timestamp)/1000 if timestamp else None,
                    quote_kind='orderbook')
