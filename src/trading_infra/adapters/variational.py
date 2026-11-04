"""Variational public RFQ indications. These are not firm executable orders."""
from datetime import datetime
from decimal import Decimal
from ..transport import PublicJSONClient
from ..venues import api_url
from ._perp import number, market_name


class VariationalPublicClient(PublicJSONClient):
    def __init__(self, network: str, *, timeout=8):
        super().__init__(api_url('variational', network), timeout=timeout)
        self.network = network

    def list_markets(self):
        data = self.get_json('/metadata/stats')
        if not isinstance(data.get('listings'), list):
            raise ValueError('Variational response missing listings')
        result = []
        for row in data['listings']:
            try:
                if row.get('name', '').startswith('Swap on '):
                    continue
                result.append(self.normalize_market(row))
            except (ValueError, KeyError, TypeError):
                continue
        return result

    @staticmethod
    def normalize_market(row):
        seconds = row.get('funding_interval_s')
        if type(seconds) is not int or seconds <= 0 or seconds % 3600:
            raise ValueError('Variational invalid or missing perpetual funding interval')
        hours = Decimal(seconds) / 3600
        apr = number(row.get('funding_rate'))
        quotes = row.get('quotes') or {}
        timestamp = None
        if quotes.get('updated_at'):
            timestamp = datetime.fromisoformat(quotes['updated_at'].replace('Z', '+00:00')).timestamp()
        base = quotes.get('base') or {}
        return dict(market=market_name(row['ticker']), symbol=row['ticker'], market_id=row['ticker'],
            quote_currency='USD', settle_currency='USDC',
            funding_rate=apr*hours/8760 if apr is not None else None,
            funding_interval_hours=hours, funding_apr=apr, funding_source='annual_simple_apr',
            next_funding_time=None, mark_price=number(row.get('mark_price'), positive=True),
            taker_fee_rate=Decimal(0), fee_source='https://docs.variational.io/omni/trading/fees',
            maintenance_margin=None, initial_margin=None,
            bid=number(base.get('bid'), positive=True), ask=number(base.get('ask'), positive=True),
            bid_size=None, ask_size=None, source_timestamp=timestamp,
            quote_kind='indicative_rfq', executable=False, reduce_only=False)

    def get_indicative_quote(self, market):
        bid, ask = market.get('bid'), market.get('ask')
        if bid is None or ask is None or bid >= ask:
            raise ValueError('Variational missing or crossed RFQ indication')
        return {k: market.get(k) for k in ('bid', 'ask', 'bid_size', 'ask_size',
                'source_timestamp', 'quote_kind', 'executable')}
