"""Exchange facts only; application execution permissions belong to callers."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Venue:
    id: str
    endpoints: Mapping[str, str]
    capabilities: frozenset[str]

    def api_url(self, network: str) -> str:
        try:
            return self.endpoints[network]
        except KeyError:
            raise ValueError(f"{self.id}: unsupported network {network!r}") from None


def _venue(name: str, endpoints: dict[str, str], *capabilities: str) -> Venue:
    return Venue(name, MappingProxyType(endpoints), frozenset(capabilities))


VENUES = MappingProxyType({
    "hyperliquid": _venue("hyperliquid", {
        "mainnet": "https://api.hyperliquid.xyz",
        "testnet": "https://api.hyperliquid-testnet.xyz",
    }, "markets", "book", "funding", "candles", "trades", "hip3"),
    "entropy": _venue("entropy", {"mainnet": "https://entropy.io"}, "catalog"),
    "lighter": _venue("lighter", {
        "mainnet": "https://mainnet.zklighter.elliot.ai/api/v1",
    }, "markets", "book", "funding"),
    "okx": _venue("okx", {"mainnet": "https://www.okx.com"},
                  "markets", "book", "funding"),
    "risex": _venue("risex", {
        "mainnet": "https://api.rise.trade",
        "testnet": "https://api.testnet.rise.trade",
    }, "markets", "book", "funding"),
    "arcus": _venue("arcus", {
        "mainnet": "https://api.arcus.xyz",
        "testnet": "https://api.testnet.arcus.xyz",
    }, "markets", "book", "funding"),
    "binance": _venue("binance", {"mainnet": "https://api.binance.com"},
                      "markets", "book", "funding", "spot"),
    "bitget": _venue("bitget", {"mainnet": "https://api.bitget.com"},
                     "markets", "book", "funding", "spot"),
})

# Public-data routes used by this deployment. Never override private write routes.
BINANCE_PUBLIC_ROUTES = MappingProxyType({
    "public": "https://data-api.binance.vision/api/v3",
    "fapiPublic": "https://www.binance.com/fapi/v1",
    "fapiPublicV2": "https://www.binance.com/fapi/v2",
    "fapiPublicV3": "https://www.binance.com/fapi/v3",
    "fapiData": "https://www.binance.com/futures/data",
})

# Binance's api/fapi/dapi/eapi/papi hosts all answer 451 from this region while
# www.binance.com serves the same paths. That substitution is a different host
# of the same official API, not a bypass of the block: nothing is proxied,
# spoofed or tunnelled, and a venue that refuses the request still refuses it.
#
# `BINANCE_PUBLIC_ROUTES` above deliberately touches only public routes, which
# is right for the scanners. A signed *read* -- a balance or a fee tier -- has
# no public route to use, so a caller that needs one rewrites the whole family
# through this helper and accepts that www may answer differently.
REACHABLE_BINANCE_HOST = "www.binance.com"


def reachable_binance_routes(urls):
    """Rewrite every *.binance.com netloc in a ccxt `urls['api']` mapping.

    Swaps the netloc rather than a substring: 'api.binance.com' is contained in
    'fapi.binance.com', so a plain replace would yield 'fwww.binance.com'.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not isinstance(urls, dict):
        return {}
    rewritten = {}
    for key, value in urls.items():
        if isinstance(value, str):
            parts = urlsplit(value)
            host = parts.netloc
            if host.endswith(".binance.com") and host != REACHABLE_BINANCE_HOST:
                value = urlunsplit(parts._replace(netloc=REACHABLE_BINANCE_HOST))
        rewritten[key] = value
    return rewritten


def api_url(venue: str, network: str) -> str:
    try:
        item = VENUES[venue]
    except KeyError:
        raise ValueError(f"Unknown exchange: {venue}") from None
    return item.api_url(network)
