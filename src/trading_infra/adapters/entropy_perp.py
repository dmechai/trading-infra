"""Read-only EntropyIO HIP-3 perpetuals, not the validator-operated perps."""
from decimal import Decimal

from ._perp import number
from .entropy import fetch_catalog
from .hyperliquid import asset_contexts
from .hyperliquid_public import HyperliquidPublicClient


class EntropyPublicClient(HyperliquidPublicClient):
    def __init__(self, network, *, timeout=8):
        if network != 'mainnet':
            raise ValueError('EntropyIO public market discovery is mainnet only')
        super().__init__(network, timeout=timeout)

    @staticmethod
    def taker_ceiling(base_taker, asset):
        # Hyperliquid's published HIP-3 formula, before account/referral and
        # aligned-collateral discounts. Budget the undiscounted public rate.
        # https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
        scale = number(asset.get('deployerFeeScale'))
        mode = asset.get('growthMode')
        if (base_taker is None or scale is None or mode not in ('enabled', 'disabled')
                or not 0 <= scale <= (1 if mode == 'enabled' else 3)):
            return None
        return base_taker * (1 + scale if scale < 1 else 2 * scale) * (
            Decimal('0.1') if mode == 'enabled' else Decimal(1))

    def list_markets(self):
        catalog = fetch_catalog(timeout=self.timeout)
        native = {r['name']: r for r in catalog.get('markets', [])
                  if r.get('dex') == 'io' and str(r.get('name')).startswith('io:')
                  and not r.get('isDelisted')}
        payload = self.post_json('/info', {'type': 'metaAndAssetCtxs', 'dex': 'io'})
        if payload[0].get('collateralToken') != 0:
            # Hyperliquid spotMeta identifies token index 0 as USDC. Do not
            # inherit the validator-perp denomination for other collateral.
            raise ValueError('Unverified EntropyIO collateral')
        try:
            base_taker = self.fee_schedule()['taker_fee_rate']
        except Exception:
            base_taker = None
        rows = []
        for name, entry in asset_contexts(payload).items():
            if name not in native:
                continue
            listing = native[name]
            try:
                row = self.normalize_market(name.removeprefix('io:'), entry['asset'], entry['ctx'])
            except (ValueError, KeyError, TypeError):
                continue
            kind = {'Stocks': 'equity', 'Crypto': 'crypto', 'Indices / ETFs': 'fund'}.get(listing.get('category'))
            if listing.get('isMarketCapBased') is True:
                kind = 'pre_ipo'
            elif listing.get('isMarketCapBased') is not False:
                kind = None
            # impactPxs are not executable top-of-book quotes. Let catalog
            # consumers fetch the namespaced l2Book rather than reuse them.
            row.update(bid=None, ask=None, quote_kind=None)
            rows.append(dict(row, symbol=name, market_id=name, asset_class=kind,
                             underlying_name=listing.get('displayName'),
                             taker_fee_rate=self.taker_ceiling(base_taker, entry['asset']),
                             fee_source='hip3_public_undiscounted'))
        if not rows:
            raise ValueError('No active EntropyIO markets verified in both catalogs')
        return rows
