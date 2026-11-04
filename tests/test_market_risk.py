"""Public risk metadata units; these tests never read account data."""
from decimal import Decimal as D
import unittest

from trading_infra.adapters.risex import RiseXPublicClient
from trading_infra.adapters.arcus import ArcusPublicClient


class MarketRiskTests(unittest.TestCase):
    def rise(self, factor='75'):
        return dict(market_id='1', config=dict(name='BTC/USDC', step_price='0.1',
                    step_size='0.000001', min_order_size='0.0001', max_leverage='50',
                    maintenance_margin_factor=factor))

    def arc(self):
        return dict(marketId=1, marketDisplayName='BTC-USD', tickSize='0.1',
                    stepSize='0.00000001', minOrderSize='0.0001', markPrice='80000',
                    initialMarginFraction='0.025', maintenanceMarginFraction='0.016667',
                    offHoursInitialMarginFraction='0.05')

    def test_rise_factor_is_a_divisor_not_percent_or_bps(self):
        row = RiseXPublicClient.normalize_market(self.rise())
        self.assertEqual(row['maintenance_margin'], D(1)/75)
        self.assertEqual(row['maintenance_margin_factor'], D(75))
        self.assertEqual(row['initial_margin'], D('.02'))
        self.assertEqual(row['max_leverage'], 50)

    def test_arcus_decimal_fractions_and_leverage(self):
        row = ArcusPublicClient.normalize_market(self.arc())
        self.assertEqual(row['maintenance_margin'], D('.016667'))
        self.assertEqual(row['initial_margin'], D('.025'))
        self.assertEqual(row['off_hours_initial_margin'], D('.05'))
        self.assertEqual(row['max_leverage'], D(40))

    def test_absent_risk_metadata_stays_unavailable(self):
        rise = self.rise(); del rise['config']['maintenance_margin_factor']
        self.assertIsNone(RiseXPublicClient.normalize_market(rise)['maintenance_margin'])
        arc = self.arc(); del arc['initialMarginFraction']; del arc['maintenanceMarginFraction']
        row = ArcusPublicClient.normalize_market(arc)
        self.assertIsNone(row['maintenance_margin'])
        self.assertIsNone(row['max_leverage'])

    def test_invalid_risk_units_never_reach_strategies(self):
        for value in ('0', '-1', 'NaN', 'Infinity', '1'):
            with self.subTest(rise=value), self.assertRaises(ValueError):
                RiseXPublicClient.normalize_market(self.rise(value))
        for key in ('initialMarginFraction', 'maintenanceMarginFraction', 'offHoursInitialMarginFraction'):
            for value in ('0', '-1', 'NaN', 'Infinity', '75'):
                row = self.arc(); row[key] = value
                with self.subTest(arc=key, value=value), self.assertRaises(ValueError):
                    ArcusPublicClient.normalize_market(row)
