"""Shared public market readers. No accounts, credentials or strategy ranking."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import time
from .adapters.arcus import ArcusPublicClient
from .adapters.risex import RiseXPublicClient
from .adapters.lighter import LighterPublicClient
from .adapters.variational import VariationalPublicClient
from .adapters.okx import OKXPublicClient
from .adapters.hyperliquid_public import HyperliquidPublicClient
from .adapters.binance_perp import BinancePerpPublicClient
from .adapters.bitget_perp import BitgetPerpPublicClient


PUBLIC_CLIENTS = {'risex': RiseXPublicClient, 'arcus': ArcusPublicClient,
                  'lighter': LighterPublicClient, 'variational': VariationalPublicClient,
                  'okx': OKXPublicClient, 'hyperliquid': HyperliquidPublicClient,
                  'binance': BinancePerpPublicClient, 'bitget': BitgetPerpPublicClient}


def public_client(venue, network, **kwargs):
    try:
        factory = PUBLIC_CLIENTS[venue]
    except KeyError:
        raise ValueError(f'Unsupported public market reader: {venue}') from None
    return factory(network, **kwargs)


def read_catalogs(clients):
    """Return each venue independently; an unavailable venue never means zero markets."""
    markets, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(clients)))) as pool:
        futures = {pool.submit(client.list_markets): venue for venue, client in clients.items()}
        for future in as_completed(futures):
            venue = futures[future]
            try:
                markets[venue] = {row['market']: dict(row, exchange=venue, received_at=time())
                                  for row in future.result()}
            except Exception as exc:
                errors[venue] = f'{type(exc).__name__}: {exc}'[:180]
    return markets, errors
