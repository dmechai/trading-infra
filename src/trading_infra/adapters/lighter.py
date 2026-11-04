"""Lighter public perpetual catalogue, hourly funding and executable depth."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from ..transport import PublicJSONClient
from ..venues import api_url
from ._perp import number, market_name, book


class LighterPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        super().__init__(api_url('lighter', network), timeout=timeout)
        self.network = network

    def _rows(self, path, key, **params):
        data = self.get_json(path, **params)
        if data.get('code') != 200 or not isinstance(data.get(key), list):
            raise ValueError(f'Lighter {path}: invalid response code={data.get("code")}')
        return data[key]

    def list_markets(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            details = pool.submit(self._rows, '/orderBookDetails', 'order_book_details')
            funding = pool.submit(self._rows, '/funding-rates', 'funding_rates')
            rates = {int(r['market_id']): number(r.get('rate')) for r in funding.result()
                     if r.get('exchange') == 'lighter'}
            result = []
            for row in details.result():
                if row.get('market_type') != 'perp' or row.get('status') != 'active':
                    continue
                try:
                    result.append(self.normalize_market(row, rates.get(int(row['market_id']))))
                except (ValueError, KeyError, TypeError):
                    continue
            return result

    @staticmethod
    def normalize_market(row, rate_8h=None):
        imr = number(row.get('min_initial_margin_fraction'), positive=True)
        mmr = number(row.get('maintenance_margin_fraction'), positive=True)
        imr = imr / 10000 if imr is not None and imr < 10000 else None
        mmr = mmr / 10000 if mmr is not None and mmr < 10000 else None
        return dict(market=market_name(row['symbol']), symbol=row['symbol'], market_id=int(row['market_id']),
            quote_currency='USD', settle_currency='USDC',
            funding_rate=rate_8h / 8 if rate_8h is not None else None,
            funding_interval_hours=Decimal(1), funding_source='8h_comparison_divided_by_8',
            mark_price=number(row.get('mark_price'), positive=True),
            index_price=number(row.get('index_price'), positive=True),
            taker_fee_rate=number(row.get('taker_fee')), initial_margin=imr, maintenance_margin=mmr,
            max_leverage=1/imr if imr else None, risk_source='lighter_orderBookDetails_bps',
            min_order_size=number(row.get('min_base_amount'), positive=True),
            min_notional=number(row.get('min_quote_amount'), positive=True),
            tick_size=Decimal(10) ** -int(row['supported_price_decimals']),
            size_step=Decimal(10) ** -int(row['supported_size_decimals']),
            reduce_only=bool(row.get('market_config', {}).get('force_reduce_only') or row.get('is_frozen')))

    def get_order_book(self, market, *, limit=20):
        if not 1 <= limit <= 250:
            raise ValueError('Lighter depth limit must be 1..250')
        data = self.get_json('/orderBookOrders', market_id=market['market_id'], limit=limit)
        if data.get('code') != 200:
            raise ValueError(f'Lighter depth code={data.get("code")}')
        return book([(r['price'], r['remaining_base_amount']) for r in data.get('bids', [])],
                    [(r['price'], r['remaining_base_amount']) for r in data.get('asks', [])],
                    quote_kind='orderbook')
