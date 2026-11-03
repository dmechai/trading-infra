"""Reusable async public CCXT quotes, independent of execution credentials."""
import asyncio
from typing import Callable

from .adapters.ccxt import create_public_exchange
from .funding import parse_interval_hours
from .models import QuoteSnapshot


class CexMarketData:
    def __init__(self, factory: Callable = create_public_exchange):
        self._factory = factory
        self._clients = {}

    async def quote(self, exchange: str, base: str) -> dict | None:
        # Do not normalize away contract multipliers or infer a quote currency.
        symbol = f"{base}/USDT:USDT"
        client = self._clients.get(exchange)
        if client is None:
            client = self._clients[exchange] = self._factory(exchange, "swap")
        ticker, book, funding = await asyncio.gather(
            client.fetch_ticker(symbol), client.fetch_order_book(symbol, 5),
            client.fetch_funding_rate(symbol),
        )
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return None
        bid, ask = float(bids[0][0]), float(asks[0][0])
        market = (getattr(client, "markets", None) or {}).get(symbol) or {}
        interval = parse_interval_hours(funding.get("interval")) or 8.0
        timestamp = book.get("timestamp")
        return QuoteSnapshot(
            exchange=exchange, base=base, symbol=symbol, bid=bid, ask=ask,
            quote_currency="USDT", settle_currency="USDT",
            contract_size=market.get("contractSize"),
            source_timestamp=float(timestamp) / 1000 if timestamp is not None else None,
            fields={
                "funding_rate": funding.get("fundingRate"), "interval_hours": interval,
                "quote_volume": ticker.get("quoteVolume"),
                "bid_notional": bid * float(bids[0][1]),
                "ask_notional": ask * float(asks[0][1]),
            },
        ).as_dict()

    async def close(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        if clients:
            await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
