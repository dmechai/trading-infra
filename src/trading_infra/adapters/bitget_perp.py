"""Bitget USDT-FUTURES public perpetuals. Public market routes only, no account."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from ..transport import PublicJSONClient
from ..venues import api_url
from ._perp import number, market_name, book

PRODUCT = 'USDT-FUTURES'


class BitgetPerpPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        if network != 'mainnet':
            raise ValueError('Bitget public perpetuals are mainnet only')
        super().__init__(api_url('bitget', network), timeout=timeout)
        self.network = network

    def _payload(self, path, **params):
        data = self.get_json(path, **params)
        if str(data.get('code')) != '00000' or data.get('data') is None:
            raise ValueError(f'Bitget {path}: code={data.get("code")} {data.get("msg", "")}')
        return data['data']

    def _rows(self, path, **params):
        # List endpoints only; depth returns a single object and uses _payload.
        data = self._payload(path, **params)
        if not isinstance(data, list):
            raise ValueError(f'Bitget {path}: expected a list payload')
        return data

    def list_markets(self):
        requests = [('contracts', '/api/v2/mix/market/contracts', {'productType': PRODUCT}),
                    ('tickers', '/api/v2/mix/market/tickers', {'productType': PRODUCT})]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {n: pool.submit(self._rows, p, **q) for n, p, q in requests}
            data = {n: f.result() for n, f in futures.items()}
        tickers = {r['symbol']: r for r in data['tickers']}
        rows = []
        for row in data['contracts']:
            if row.get('symbolStatus') != 'normal' or row.get('quoteCoin') != 'USDT':
                continue
            try:
                rows.append(self.normalize_market(row, tickers.get(row['symbol'], {})))
            except (ValueError, KeyError, TypeError):
                continue
        return rows

    @staticmethod
    def normalize_market(row, ticker):
        bid = number(ticker.get('bidPr'), positive=True)
        ask = number(ticker.get('askPr'), positive=True)
        if bid is None or ask is None or bid >= ask:
            bid = ask = None
        places = row.get('volumePlace')
        step = Decimal(1).scaleb(-int(places)) if str(places).lstrip('-').isdigit() else None
        ticks = row.get('pricePlace')
        tick = Decimal(1).scaleb(-int(ticks)) if str(ticks).lstrip('-').isdigit() else None
        hours = number(row.get('fundInterval'), positive=True)
        nxt = number(ticker.get('nextFundingTime'), positive=True)
        timestamp = number(ticker.get('ts'), positive=True)
        return dict(market=market_name(row['baseCoin']), symbol=row['symbol'], market_id=row['symbol'],
                    quote_currency='USDT', settle_currency='USDT', contract_size=Decimal(1),
                    funding_rate=number(ticker.get('fundingRate')),
                    funding_interval_hours=hours if hours else Decimal(8),
                    next_funding_time=int(nxt/1000) if nxt else None,
                    mark_price=number(ticker.get('markPrice'), positive=True), taker_fee_rate=None,
                    max_leverage=number(row.get('maxLever'), positive=True),
                    maintenance_margin=None, initial_margin=None,
                    tick_size=tick, size_step=step,
                    min_order_size=number(row.get('minTradeNum'), positive=True),
                    bid=bid, ask=ask,
                    bid_size=number(ticker.get('bidSz'), positive=True) if bid is not None else None,
                    ask_size=number(ticker.get('askSz'), positive=True) if ask is not None else None,
                    source_timestamp=float(timestamp)/1000 if timestamp else None,
                    quote_kind='orderbook', reduce_only=False)

    def get_order_book(self, market, *, limit=20):
        allowed = (1, 5, 15, 50, 100)
        depth = next((n for n in allowed if n >= limit), None)
        if depth is None:
            raise ValueError('Bitget depth limit must be <= 100')
        data = self._payload('/api/v2/mix/market/merge-depth', symbol=market['symbol'],
                             productType=PRODUCT, limit=str(depth))
        raw = data[0] if isinstance(data, list) and data else data
        if not isinstance(raw, dict) or not raw.get('bids') or not raw.get('asks'):
            raise ValueError('Bitget missing depth')
        timestamp = number(raw.get('ts'), positive=True)
        return book([(p, q) for p, q in raw['bids'][:limit]],
                    [(p, q) for p, q in raw['asks'][:limit]],
                    source_timestamp=float(timestamp)/1000 if timestamp else None,
                    quote_kind='orderbook')
