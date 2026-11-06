import unittest
from decimal import Decimal as D
from unittest.mock import Mock

from trading_infra.adapters.lighter import LighterPublicClient
from trading_infra.adapters.okx import OKXPublicClient
from trading_infra.adapters.variational import VariationalPublicClient
from trading_infra.catalog import public_client, read_catalogs


class PublicPerpetualTests(unittest.TestCase):
    def lighter_row(self):
        return dict(symbol='BTC', market_id=1, market_type='perp', status='active',
                    supported_price_decimals=1, supported_size_decimals=6,
                    taker_fee='0.0000', min_initial_margin_fraction=200,
                    maintenance_margin_fraction=120, mark_price='70000')

    def test_lighter_filters_other_exchanges_and_normalizes_eight_hour_rate(self):
        c = LighterPublicClient('mainnet')
        c.get_json = Mock(side_effect=lambda path, **kw: {'code': 200, **(
            {'order_book_details': [self.lighter_row(), dict(self.lighter_row(), status='inactive')]}
            if path == '/orderBookDetails' else {'funding_rates': [
                dict(market_id=1, exchange='binance', rate='.8'),
                dict(market_id=1, exchange='lighter', rate='.0001')]})})
        row = c.list_markets()[0]
        self.assertEqual(row['funding_rate'], D('.0000125'))
        self.assertEqual(row['funding_interval_hours'], 1)
        self.assertEqual(row['maintenance_margin'], D('.012'))
        self.assertEqual(row['initial_margin'], D('.02'))
        self.assertEqual(row['max_leverage'], 50)
        self.assertEqual(row['taker_fee_rate'], 0)
        self.assertIsNone(c.normalize_market(self.lighter_row())['funding_rate'])

    def test_lighter_depth_is_base_quantity_and_invalid_business_code_raises(self):
        c = LighterPublicClient('mainnet')
        c.get_json = Mock(return_value={'code': 200, 'bids': [dict(price='100', remaining_base_amount='2')],
                                      'asks': [dict(price='101', remaining_base_amount='3')]})
        self.assertEqual(c.get_order_book({'market_id': 1})['bid_size'], 2)
        c.get_json.return_value = {'code': 23000, 'funding_rates': []}
        with self.assertRaises(ValueError):
            c.list_markets()

    def test_variational_apr_and_rfq_are_not_executable(self):
        row = VariationalPublicClient.normalize_market(dict(ticker='RE_ETH', funding_rate='.1095',
            funding_interval_s=14400, mark_price='100', quotes={'updated_at': '2026-11-04T05:00:00Z',
            'base': {'bid': '99', 'ask': '101'}}))
        self.assertEqual(row['funding_rate'], D('.00005'))
        self.assertEqual(row['market'], 'RE_ETH-USD')
        self.assertFalse(row['executable'])
        self.assertEqual(row['taker_fee_rate'], 0)
        for interval in (0, -1, 1800, None):
            with self.assertRaises(ValueError):
                VariationalPublicClient.normalize_market(dict(ticker='BTC', funding_interval_s=interval))

    def okx_row(self, **changes):
        return dict(dict(instId='BTC-USDT-SWAP', ctVal='0.01', ctMult='1', ctValCcy='BTC',
                         lotSz='1', minSz='1', tickSz='.1'), **changes)

    def test_okx_contract_conversion_and_actual_funding_interval(self):
        row = OKXPublicClient.normalize_market(self.okx_row(),
            {'fundingRate': '.0002', 'fundingTime': '28800000', 'prevFundingTime': '14400000'},
            {'bidPx': '100', 'askPx': '101', 'bidSz': '200', 'askSz': '300', 'ts': '20000000'}, {})
        self.assertEqual(row['funding_interval_hours'], 4)
        self.assertEqual(row['bid_size'], D('2'))
        self.assertEqual(row['ask_size'], D('3'))
        self.assertEqual(row['size_step'], D('.01'))
        self.assertIsNone(row['taker_fee_rate'])
        self.assertEqual(row['quote_currency'], 'USDT')
        with self.assertRaises(ValueError):
            OKXPublicClient.normalize_market(self.okx_row(ctValCcy='USD'), {}, {}, {})

    def test_okx_crossed_and_unknown_funding_stay_missing(self):
        row = OKXPublicClient.normalize_market(self.okx_row(), {}, {'bidPx': '102', 'askPx': '101'}, {})
        self.assertIsNone(row['bid'])
        self.assertIsNone(row['ask'])
        self.assertIsNone(row['funding_interval_hours'])
        c = OKXPublicClient('mainnet')
        c.get_json = Mock(return_value={'code': '51000', 'data': []})
        with self.assertRaises(ValueError):
            c.list_markets()

    def test_catalog_failure_is_isolated_and_network_does_not_fall_back(self):
        good, bad = Mock(), Mock()
        good.list_markets.return_value = [dict(market='SOL-USD')]
        bad.list_markets.side_effect = ValueError('unavailable')
        rows, errors = read_catalogs({'good': good, 'bad': bad})
        self.assertIn('SOL-USD', rows['good'])
        self.assertIn('bad', errors)
        self.assertNotIn('bad', rows)
        for venue in ('lighter', 'variational', 'okx'):
            with self.assertRaises(ValueError):
                public_client(venue, 'testnet')


from importlib.util import find_spec

# `dex` and the async readers live behind the `async` extra (aiohttp, ccxt).
# Without it these cannot run at all, so say so instead of reporting a failure.
ASYNC_EXTRA = find_spec("aiohttp") is not None and find_spec("ccxt") is not None
REQUIRES_ASYNC_EXTRA = unittest.skipUnless(
    ASYNC_EXTRA, "install trading-infra[async] (aiohttp, ccxt) to run the async readers")


@REQUIRES_ASYNC_EXTRA
class AsyncPublicPerpetualTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_async_lighter_reader_uses_hourly_rate_and_preserves_missing(self):
        from unittest.mock import patch, AsyncMock
        from trading_infra import dex
        try:
            with patch.object(dex, '_get_json', new=AsyncMock(return_value={'code': 200, 'funding_rates': [
                {'exchange': 'binance', 'symbol': 'BTC', 'rate': '.99'},
                {'exchange': 'lighter', 'symbol': 'BTC', 'rate': '.0008'},
                {'exchange': 'lighter', 'symbol': 'SOL', 'rate': 0},
                {'exchange': 'lighter', 'symbol': 'ETH', 'rate': None}]})):
                rates = await dex._lighter_funding()
            self.assertEqual(rates['BTC'], .0001)
            self.assertEqual(rates['SOL'], 0)
            self.assertNotIn('ETH', rates)
        finally:
            await dex.close()

    async def test_new_async_readers_preserve_rfq_and_catalog_cache_timestamp(self):
        from unittest.mock import patch
        from trading_infra import dex
        client = Mock()
        client.list_markets.return_value = [dict(market='BTC-USD', symbol='BTC', bid=D('100'), ask=D('101'),
            mark_price=D('100'), funding_rate=D('.0001'), funding_interval_hours=D('8'),
            taker_fee_rate=D('0'), quote_currency='USD', settle_currency='USDC',
            quote_kind='indicative_rfq', executable=False, source_timestamp=100)]
        try:
            with patch('trading_infra.catalog.public_client', return_value=client):
                first = await dex.variational_quote('BTC')
                second = await dex.variational_quote('BTC')
            self.assertFalse(first['executable'])
            self.assertIsNone(first['bid_notional'])
            self.assertEqual(first['received_at'], second['received_at'])
            self.assertEqual(first['source_timestamp'], 100)
            self.assertEqual(client.list_markets.call_count, 1)
            self.assertIn('okx', dex.QUOTE_ADAPTERS)
        finally:
            await dex.close()


if __name__ == '__main__':
    unittest.main()
