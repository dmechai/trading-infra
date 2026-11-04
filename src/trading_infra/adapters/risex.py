"""RiseX public REST reads. No wallet, signing or order writes.

Network must be explicit; testnet never borrows mainnet market metadata.
"""
from __future__ import annotations

from decimal import Decimal

from ..transport import PublicJSONClient
from ..venues import VENUES, api_url


class RiseXPublicClient(PublicJSONClient):
    API_URLS = dict(VENUES["risex"].endpoints)

    def __init__(self, network: str, *, timeout: float = 8):
        super().__init__(api_url("risex", network), timeout=timeout)
        self.network = network

    def _get(self, path: str, **params):
        result = self.get_json(path, **params)
        if not isinstance(result, dict) or "data" not in result:
            raise ValueError("RiseX response missing data envelope")
        return result["data"]

    def list_markets(self) -> list[dict]:
        payload = self._get("/v1/markets")
        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise ValueError("RiseX response missing markets")
        return [self.normalize_market(row) for row in payload["markets"]
                if row.get("active") and row.get("config", {}).get("unlocked")
                and row.get("config", {}).get("name", "").endswith("/USDC")]

    @staticmethod
    def normalize_market(row: dict) -> dict:
        config = row["config"]
        symbol = config["name"]
        base, quote = symbol.split("/")
        if quote != "USDC":
            raise ValueError(f"Unsupported RiseX quote: {quote}")
        tick = Decimal(config["step_price"])
        step = Decimal(config["step_size"])
        minimum = Decimal(config["min_order_size"])
        leverage = Decimal(config["max_leverage"])
        if min(tick, step, minimum, leverage) <= 0:
            raise ValueError("RiseX market filters must be positive")
        factor = Decimal(str(config['maintenance_margin_factor'])) if config.get('maintenance_margin_factor') is not None else None
        if factor is not None and (not factor.is_finite() or factor <= 1):
            raise ValueError("RiseX maintenance margin factor must be finite and greater than one")
        funding_rate = row.get("current_funding_rate")
        funding_interval = row.get("funding_interval")
        mark_price = row.get("mark_price") or row.get("last_price")
        next_funding_time = row.get("next_funding_time")
        return {
            "market": f"{base}-USD",
            "symbol": symbol,
            "market_id": int(row["market_id"]),
            "tick_size": float(tick),
            "size_step": float(step),
            "min_order_size": float(minimum),
            "max_leverage": float(leverage),
            "initial_margin": Decimal(1) / leverage,
            # Official RiseX UI: maintenance = abs(size) * mark / factor.
            "maintenance_margin_factor": factor,
            "maintenance_margin": Decimal(1) / factor if factor is not None else None,
            "risk_source": "risex_market_config",
            "reduce_only": bool(row.get("reduce_only")),
            "unlocked": bool(config.get("unlocked")),
            "funding_rate": Decimal(str(funding_rate)) if funding_rate is not None else None,
            "funding_interval_hours": Decimal(str(funding_interval)) / Decimal(3_600_000_000_000) if funding_interval is not None else None,
            "next_funding_time": (int(next_funding_time) // 1_000_000_000 if int(next_funding_time or 0) > 10**12 else int(next_funding_time or 0)),
            "mark_price": Decimal(str(mark_price)) if mark_price is not None else None,
        }

    def get_order_book(self, market: dict, *, limit: int = 20) -> dict:
        payload = self._get("/v1/orderbook", market_id=market["market_id"], limit=limit)
        if int(payload["market_id"]) != market["market_id"]:
            raise ValueError("RiseX orderbook market id mismatch")
        bids = [(float(row["price"]), float(row["quantity"])) for row in payload["bids"]]
        asks = [(float(row["price"]), float(row["quantity"])) for row in payload["asks"]]
        if not bids or not asks:
            raise ValueError("RiseX orderbook has an empty side")
        bid, bid_size = max(bids)
        ask, ask_size = min(asks)
        if bid <= 0 or bid >= ask or min(bid_size, ask_size) <= 0:
            raise ValueError("RiseX orderbook is invalid or crossed")
        return {"bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size,
                "tick_size": market["tick_size"], "bids": bids, "asks": asks}
