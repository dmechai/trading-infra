import unittest
from decimal import Decimal as D
from unittest.mock import Mock

from trading_infra.adapters.binance_perp import BinancePerpPublicClient
from trading_infra.adapters.bitget_perp import BitgetPerpPublicClient
from trading_infra.adapters.hyperliquid_public import HyperliquidPublicClient
from trading_infra.catalog import PUBLIC_CLIENTS, public_client


class CatalogRegistrationTests(unittest.TestCase):
    def test_every_scanner_venue_has_a_public_reader(self):
        for venue in ('risex', 'arcus', 'lighter', 'variational', 'okx',
                      'hyperliquid', 'binance', 'bitget'):
            self.assertIn(venue, PUBLIC_CLIENTS, venue)

    def test_cex_readers_refuse_a_network_they_do_not_serve(self):
        for venue in ('binance', 'bitget'):
            with self.assertRaises(ValueError):
                public_client(venue, 'testnet')


class HyperliquidPublicTests(unittest.TestCase):
    def payload(self, **asset):
        return [{'universe': [dict({'name': 'BTC', 'szDecimals': 5, 'maxLeverage': 40}, **asset)]},
                [{'funding': '0.0000125', 'markPx': '80396.0', 'impactPxs': ['80376.0', '80377.0']}]]

    def test_normalizes_hourly_funding_and_impact_quotes(self):
        c = HyperliquidPublicClient('mainnet')
        c.post_json = Mock(return_value=self.payload())
        row = c.list_markets()[0]
        self.assertEqual(row['market'], 'BTC-USD')
        self.assertEqual(row['funding_interval_hours'], D(1))
        self.assertEqual(row['size_step'], D('0.00001'))
        self.assertEqual(row['settle_currency'], 'USDC')
        # impactPxs are an impact-notional average, never top of book.
        self.assertEqual(row['quote_kind'], 'impact')
        self.assertIsNone(row['bid_size'])

    def test_delisted_and_crossed_quotes_are_dropped_not_guessed(self):
        c = HyperliquidPublicClient('mainnet')
        c.post_json = Mock(return_value=self.payload(isDelisted=True))
        self.assertEqual(c.list_markets(), [])
        c.post_json = Mock(return_value=[
            {'universe': [{'name': 'BTC', 'szDecimals': 5}]},
            [{'funding': '0', 'markPx': '1', 'impactPxs': ['2', '1']}]])
        self.assertIsNone(c.list_markets()[0]['bid'])

    def test_order_book_reads_both_sides(self):
        c = HyperliquidPublicClient('mainnet')
        c.post_json = Mock(return_value={'time': 1700000000000, 'levels': [
            [{'px': '99', 'sz': '3'}], [{'px': '101', 'sz': '4'}]]})
        b = c.get_order_book({'symbol': 'BTC'}, limit=1)
        self.assertEqual((b['bid'], b['ask'], b['bid_size']), (D(99), D(101), D(3)))


class BinancePerpTests(unittest.TestCase):
    def info(self, **over):
        return {'symbols': [dict({
            'symbol': 'BTCUSDT', 'baseAsset': 'BTC', 'quoteAsset': 'USDT', 'marginAsset': 'USDT',
            'status': 'TRADING', 'contractType': 'PERPETUAL', 'filters': [
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.10'},
                {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'}]}, **over)]}

    def client(self, **over):
        c = BinancePerpPublicClient('mainnet')
        c.get_json = Mock(side_effect=lambda path, **kw: {
            '/exchangeInfo': self.info(**over),
            '/ticker/bookTicker': [{'symbol': 'BTCUSDT', 'bidPrice': '80367.20', 'askPrice': '80367.30',
                                    'bidQty': '2.5', 'askQty': '12.4', 'time': 1700000000000}],
            '/premiumIndex': [{'symbol': 'BTCUSDT', 'lastFundingRate': '0.0001',
                               'markPrice': '80367.25', 'nextFundingTime': 1700028800000}],
        }[path])
        return c

    def test_normalizes_linear_usdt_perpetual(self):
        row = self.client().list_markets()[0]
        self.assertEqual((row['market'], row['size_step']), ('BTC-USD', D('0.001')))
        self.assertEqual(row['funding_interval_hours'], D(8))
        self.assertEqual(row['funding_rate'], D('0.0001'))
        self.assertEqual(row['bid_size'], D('2.5'))

    def test_non_perpetual_and_halted_symbols_are_excluded(self):
        self.assertEqual(self.client(contractType='CURRENT_QUARTER').list_markets(), [])
        self.assertEqual(self.client(status='BREAK').list_markets(), [])
        self.assertEqual(self.client(quoteAsset='BUSD').list_markets(), [])

    def test_declared_funding_interval_overrides_the_eight_hour_default(self):
        self.assertEqual(self.client(fundingIntervalHours='4').list_markets()[0]['funding_interval_hours'], D(4))


class BitgetPerpTests(unittest.TestCase):
    def contract(self, **over):
        return dict({'symbol': 'BTCUSDT', 'baseCoin': 'BTC', 'quoteCoin': 'USDT',
                     'symbolStatus': 'normal', 'volumePlace': '4', 'pricePlace': '1',
                     'minTradeNum': '0.0001', 'maxLever': '125'}, **over)

    def client(self, **over):
        c = BitgetPerpPublicClient('mainnet')
        c.get_json = Mock(side_effect=lambda path, **kw: {'code': '00000', 'msg': 'success', 'data': {
            '/api/v2/mix/market/contracts': [self.contract(**over)],
            '/api/v2/mix/market/tickers': [{'symbol': 'BTCUSDT', 'bidPr': '80351.5', 'askPr': '80351.6',
                                            'bidSz': '2.37', 'askSz': '3.02', 'fundingRate': '-0.000095',
                                            'markPrice': '80351.0', 'ts': '1700000000000'}],
        }[path]})
        return c

    def test_normalizes_usdt_futures_and_negative_funding(self):
        row = self.client().list_markets()[0]
        self.assertEqual((row['market'], row['size_step'], row['tick_size']), ('BTC-USD', D('0.0001'), D('0.1')))
        self.assertEqual(row['funding_rate'], D('-0.000095'))
        self.assertEqual(row['max_leverage'], D(125))

    def test_offline_symbols_are_excluded(self):
        self.assertEqual(self.client(symbolStatus='maintain').list_markets(), [])

    def test_depth_accepts_the_object_payload_that_list_endpoints_do_not_return(self):
        c = BitgetPerpPublicClient('mainnet')
        c.get_json = Mock(return_value={'code': '00000', 'data': {
            'bids': [['99', '3']], 'asks': [['101', '4']], 'ts': '1700000000000'}})
        b = c.get_order_book({'symbol': 'BTCUSDT'}, limit=1)
        self.assertEqual((b['bid'], b['ask']), (D(99), D(101)))
        # A list endpoint must still reject a non-list payload rather than guess.
        with self.assertRaises(ValueError):
            c._rows('/api/v2/mix/market/contracts')
