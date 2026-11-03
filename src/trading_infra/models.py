"""Time and identity metadata shared by public quote adapters."""
from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(frozen=True)
class QuoteSnapshot:
    exchange: str
    symbol: str
    base: str
    bid: float
    ask: float
    network: str = "mainnet"
    market_type: str = "swap"
    quote_currency: str | None = None
    settle_currency: str | None = None
    contract_size: float | None = None
    received_at: float = field(default_factory=time)
    source_timestamp: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.fields, "exchange": self.exchange, "symbol": self.symbol,
                "base": self.base, "bid": self.bid, "ask": self.ask,
                "network": self.network, "market_type": self.market_type,
                "quote_currency": self.quote_currency, "settle_currency": self.settle_currency,
                "contract_size": self.contract_size, "received_at": self.received_at,
                "source_timestamp": self.source_timestamp}

    def is_fresh(self, max_age: float, *, now: float | None = None) -> bool:
        age = (time() if now is None else now) - self.received_at
        return 0 <= age <= max_age
