"""Read-only public quotes with explicit session shutdown and per-process caching."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp


from .adapters.hyperliquid import asset_contexts
from .venues import api_url
from .symbols import native_perp_base as _base

HYPERLIQUID_INFO = api_url("hyperliquid", "mainnet") + "/info"
LIGHTER_API = api_url("lighter", "mainnet")
_CACHE_TTL = 20.0
_BOOK_TTL = 3.0
_cache: dict[str, tuple[float, Any]] = {}
_session: aiohttp.ClientSession | None = None
_lock = asyncio.Lock()


async def _session_get() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=12)
        _session = aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/json"})
    return _session


async def close() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
    _cache.clear()


async def _cached(key: str, ttl: float, loader):
    now = time.monotonic()
    found = _cache.get(key)
    if found and now - found[0] < ttl:
        return found[1]
    async with _lock:
        found = _cache.get(key)
        now = time.monotonic()
        if found and now - found[0] < ttl:
            return found[1]
        value = await loader()
        _cache[key] = (time.monotonic(), value)
        return value


async def _post_json(url: str, payload: dict[str, Any]) -> Any:
    session = await _session_get()
    async with session.post(url, json=payload) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def _get_json(url: str) -> Any:
    session = await _session_get()
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def _hyperliquid_contexts() -> dict[str, dict[str, Any]]:
    async def load():
        data = await _post_json(HYPERLIQUID_INFO, {"type": "metaAndAssetCtxs"})
        return {name.upper(): row for name, row in asset_contexts(data).items()}
    return await _cached("hyperliquid:contexts", _CACHE_TTL, load)


async def _lighter_markets() -> dict[str, dict[str, Any]]:
    async def load():
        data = await _get_json(f"{LIGHTER_API}/orderBooks")
        result = {}
        for row in data.get("order_books") or []:
            if row.get("market_type") == "perp" and row.get("status") == "active":
                result[str(row.get("symbol") or "").upper()] = row
        return result
    return await _cached("lighter:markets", _CACHE_TTL, load)


async def _lighter_funding() -> dict[str, float]:
    async def load():
        data = await _get_json(f"{LIGHTER_API}/funding-rates")
        if data.get('code') != 200:
            raise ValueError(f'Lighter funding code={data.get("code")}')
        result = {}
        for row in data.get("funding_rates") or []:
            if str(row.get("exchange") or "").lower() == "lighter":
                rate = row.get('rate')
                if rate is not None:
                    result[str(row.get("symbol") or "").upper()] = float(rate) / 8
        return result
    return await _cached("lighter:funding", _CACHE_TTL, load)


async def hyperliquid_quote(symbol: str) -> dict[str, Any] | None:
    base = _base(symbol)
    contexts = await _hyperliquid_contexts()
    row = contexts.get(base)
    if not row:
        return None
    ctx = row["ctx"]
    book = await _cached(
        f"hyperliquid:book:{base}",
        _BOOK_TTL,
        lambda: _post_json(HYPERLIQUID_INFO, {"type": "l2Book", "coin": base}),
    )
    levels = book.get("levels") or [[], []]
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    if not bids or not asks:
        return None
    bid, ask = float(bids[0]["px"]), float(asks[0]["px"])
    bid_qty, ask_qty = float(bids[0]["sz"]), float(asks[0]["sz"])
    mark = float(ctx.get("markPx") or ((bid + ask) / 2.0))
    return {
        "exchange": "hyperliquid", "base": base, "symbol": f"{base}/USDC:USDC",
        "bid": bid, "ask": ask, "mark": mark, "funding_rate": float(ctx.get("funding") or 0.0),
        "interval_hours": 1.0, "quote_volume": float(ctx.get("dayNtlVlm") or 0.0),
        "bid_notional": bid * bid_qty, "ask_notional": ask * ask_qty,
        "open_interest": float(ctx.get("openInterest") or 0.0), "source": "hyperliquid-public",
    }


async def lighter_quote(symbol: str) -> dict[str, Any] | None:
    base = _base(symbol)
    markets = await _lighter_markets()
    market = markets.get(base)
    if not market:
        return None
    market_id = int(market["market_id"])
    funding = await _lighter_funding()
    book = await _cached(
        f"lighter:book:{market_id}",
        _BOOK_TTL,
        lambda: _get_json(f"{LIGHTER_API}/orderBookOrders?market_id={market_id}&limit=5"),
    )
    bids = sorted((book.get("bids") or []), key=lambda row: float(row.get("price") or 0.0), reverse=True)
    asks = sorted((book.get("asks") or []), key=lambda row: float(row.get("price") or 0.0))
    if not bids or not asks:
        return None
    bid, ask = float(bids[0]["price"]), float(asks[0]["price"])
    bid_qty = float(bids[0].get("remaining_base_amount") or 0.0)
    ask_qty = float(asks[0].get("remaining_base_amount") or 0.0)
    return {
        "exchange": "lighter", "base": base, "symbol": f"{base}/USDC:USDC",
        "bid": bid, "ask": ask, "mark": (bid + ask) / 2.0,
        "funding_rate": funding.get(base), "interval_hours": 1.0,
        "quote_volume": float(market.get("open_interest_usd") or 0.0),
        "bid_notional": bid * bid_qty, "ask_notional": ask * ask_qty,
        "open_interest": float(market.get("open_interest") or 0.0), "source": "lighter-public",
    }


async def _catalog_quote(venue: str, symbol: str) -> dict[str, Any] | None:
    from .catalog import public_client

    async def load():
        rows = await asyncio.to_thread(public_client(venue, 'mainnet').list_markets)
        received = time.time()
        return {row['market']: dict(row, received_at=received) for row in rows}

    markets = await _cached(venue+':catalog', _CACHE_TTL, load)
    base = _base(symbol)
    row = markets.get(base+'-USD')
    if not row or row.get('bid') is None or row.get('ask') is None or row['bid'] >= row['ask']:
        return None
    def value(key):
        return float(row[key]) if row.get(key) is not None else None
    bid, ask = value('bid'), value('ask')
    return dict(exchange=venue, base=base, symbol=row['symbol'], network='mainnet',
        bid=bid, ask=ask, mark=value('mark_price'), funding_rate=value('funding_rate'),
        interval_hours=value('funding_interval_hours'), taker_fee_rate=value('taker_fee_rate'),
        bid_notional=bid*float(row['bid_size']) if row.get('bid_size') is not None else None,
        ask_notional=ask*float(row['ask_size']) if row.get('ask_size') is not None else None,
        quote_currency=row['quote_currency'], settle_currency=row['settle_currency'],
        contract_size=value('contract_size'), source_timestamp=row.get('source_timestamp'),
        received_at=row['received_at'], quote_kind=row.get('quote_kind'),
        executable=row.get('executable', True), source=venue+'-public')


async def okx_quote(symbol: str) -> dict[str, Any] | None:
    return await _catalog_quote('okx', symbol)


async def variational_quote(symbol: str) -> dict[str, Any] | None:
    return await _catalog_quote('variational', symbol)


QUOTE_ADAPTERS = {
    'okx': okx_quote,
    'variational': variational_quote,
    "hyperliquid": hyperliquid_quote,
    "lighter": lighter_quote,
}


def register_quote_adapter(exchange: str, adapter) -> None:
    """Register an async adapter; applications still choose which venues to scan."""
    QUOTE_ADAPTERS[str(exchange).lower()] = adapter


async def quote(exchange: str, symbol: str) -> dict[str, Any] | None:
    adapter = QUOTE_ADAPTERS.get(str(exchange or "").lower())
    return await adapter(symbol) if adapter is not None else None


async def scan_quotes(bases: list[str], exchanges=("hyperliquid", "lighter")) -> list[dict[str, Any]]:
    jobs = [quote(exchange, base) for base in bases for exchange in exchanges]
    rows = await asyncio.gather(*jobs, return_exceptions=True)
    failures = [row for row in rows if isinstance(row, Exception)]
    if failures and len(failures) == len(rows):
        raise RuntimeError(f"{type(failures[0]).__name__}: {failures[0]}")
    return [row for row in rows if isinstance(row, dict)]
