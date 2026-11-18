import unittest
from decimal import Decimal
from unittest.mock import patch

from trading_infra.adapters.entropy_perp import EntropyPublicClient


class EntropyPublicTests(unittest.TestCase):
    def test_native_catalog_preserves_book_symbol_and_uses_hip3_cost(self):
        listing = dict(dex='io', name='io:SNDK', displayName='Sandisk',
                       category='Stocks', isMarketCapBased=False)
        token = dict(name='io:SNDK', szDecimals=3, growthMode='enabled', deployerFeeScale='1')
        with patch('trading_infra.adapters.entropy_perp.fetch_catalog', return_value={'markets': [listing]}), \
             patch.object(EntropyPublicClient, 'fee_schedule', return_value={'taker_fee_rate': Decimal('.00045')}), \
             patch.object(EntropyPublicClient, 'post_json', side_effect=[
                 [{'collateralToken': 0, 'universe': [token, dict(token, name='other:SNDK')]},
                  [{'markPx': '100', 'funding': '0.001', 'impactPxs': ['99', '101']}] * 2],
                 {'time': 1712000000123, 'levels': [[{'px': '99', 'sz': '2'}], [{'px': '101', 'sz': '3'}]]}
             ]) as post:
            client = EntropyPublicClient('mainnet')
            row, = client.list_markets()
            result = client.get_order_book(row, limit=1)
        self.assertEqual((row['market'], row['symbol'], row['asset_class']), ('SNDK-USD', 'io:SNDK', 'equity'))
        self.assertEqual(row['settle_currency'], 'USDC')
        self.assertEqual(row['taker_fee_rate'], Decimal('.00009'))
        self.assertIsNone(row['bid'])
        self.assertIsNone(row['quote_kind'])
        self.assertEqual(result['ask_size'], Decimal('3'))
        self.assertEqual(post.call_args_list[0].args[1], {'type': 'metaAndAssetCtxs', 'dex': 'io'})
        self.assertEqual(post.call_args_list[-1].args[1], {'type': 'l2Book', 'coin': 'io:SNDK'})

    def test_missing_fee_metadata_is_not_the_validator_fee(self):
        self.assertIsNone(EntropyPublicClient.taker_ceiling(Decimal('.00045'), {'deployerFeeScale': '1'}))
