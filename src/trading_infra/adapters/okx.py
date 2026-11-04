"""OKX public linear USDT swaps; contract quantities converted to base units."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from ..transport import PublicJSONClient
from ..venues import api_url
from ._perp import number, market_name, book


class OKXPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        super().__init__(api_url('okx', network), timeout=timeout)
        self.network = network

    def _rows(self, path, **params):
        data = self.get_json(path, **params)
        if data.get('code') != '0' or not isinstance(data.get('data'), list):
            raise ValueError(f'OKX {path}: code={data.get("code")} {data.get("msg", "")}')
        return data['data']

    def list_markets(self):
        requests = [('instruments', '/api/v5/public/instruments', {'instType': 'SWAP'}),
                    ('funding', '/api/v5/public/funding-rate', {'instId': 'ANY'}),
                    ('tickers', '/api/v5/market/tickers', {'instType': 'SWAP'}),
                    ('marks', '/api/v5/public/mark-price', {'instType': 'SWAP'})]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(self._rows, path, **params) for name, path, params in requests}
            data = {name: future.result() for name, future in futures.items()}
        funding = {r['instId']: r for r in data['funding']}
        tickers = {r['instId']: r for r in data['tickers']}
        marks = {r['instId']: r for r in data['marks']}
        result = []
        for row in data['instruments']:
            if (row.get('state') != 'live' or row.get('ctType') != 'linear'
                    or row.get('settleCcy') != 'USDT' or not row.get('instId', '').endswith('-USDT-SWAP')):
                continue
            try:
                key = row['instId']
                result.append(self.normalize_market(row, funding.get(key, {}), tickers.get(key, {}), marks.get(key, {})))
            except (ValueError, KeyError, TypeError):
                continue
        return result

    @staticmethod
    def normalize_market(row, funding, ticker, mark):
        base = row['instId'][:-len('-USDT-SWAP')]
        contract = number(row.get('ctVal'), positive=True)
        multiplier = number(row.get('ctMult'), positive=True)
        if row.get('ctValCcy') != base or contract is None or multiplier is None:
            raise ValueError('OKX unknown contract denomination')
        contract *= multiplier
        current = number(funding.get('fundingTime'), positive=True)
        previous = number(funding.get('prevFundingTime'), positive=True)
        interval = (current-previous)/3600000 if current is not None and previous is not None and current > previous else None
        bid, ask = number(ticker.get('bidPx'), positive=True), number(ticker.get('askPx'), positive=True)
        if bid is None or ask is None or bid >= ask:
            bid = ask = None
        bid_qty, ask_qty = number(ticker.get('bidSz'), positive=True), number(ticker.get('askSz'), positive=True)
        timestamp = number(ticker.get('ts'), positive=True)
        return dict(market=market_name(base), symbol=row['instId'], market_id=row['instId'],
            quote_currency='USDT', settle_currency='USDT', contract_size=contract,
            funding_rate=number(funding.get('fundingRate')), funding_interval_hours=interval,
            next_funding_time=int(current/1000) if current is not None else None,
            mark_price=number(mark.get('markPx'), positive=True), taker_fee_rate=None,
            max_leverage=number(row.get('lever'), positive=True), maintenance_margin=None, initial_margin=None,
            tick_size=number(row.get('tickSz'), positive=True),
            size_step=number(row.get('lotSz'), positive=True)*contract if number(row.get('lotSz'), positive=True) else None,
            min_order_size=number(row.get('minSz'), positive=True)*contract if number(row.get('minSz'), positive=True) else None,
            bid=bid, ask=ask, bid_size=bid_qty*contract if bid is not None and bid_qty else None,
            ask_size=ask_qty*contract if ask is not None and ask_qty else None,
            source_timestamp=float(timestamp/1000) if timestamp else None, quote_kind='orderbook', reduce_only=False)

    def get_order_book(self, market, *, limit=20):
        if not 1 <= limit <= 400:
            raise ValueError('OKX depth limit must be 1..400')
        rows = self._rows('/api/v5/market/books', instId=market['symbol'], sz=limit)
        if not rows:
            raise ValueError('OKX missing depth')
        row = rows[0]
        contract = market['contract_size']
        return book([(r[0], Decimal(r[1])*contract) for r in row['bids']],
                    [(r[0], Decimal(r[1])*contract) for r in row['asks']],
                    source_timestamp=float(row['ts'])/1000, quote_kind='orderbook')
