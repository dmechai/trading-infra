"""Arcus public market reads. No wallet, signing, or order writes."""
from __future__ import annotations

from decimal import Decimal

from ..transport import PublicJSONClient
from ..venues import VENUES, api_url


class ArcusPublicClient(PublicJSONClient):
    API_URLS = dict(VENUES["arcus"].endpoints)

    def __init__(self, network: str, *, timeout: float = 8):
        super().__init__(api_url("arcus", network), timeout=timeout)
        self.network = network

    def _get(self, path: str):
        result = self.get_json(path)
        if not isinstance(result, dict):
            raise ValueError("Arcus response is not an object")
        return result

    def list_markets(self) -> list[dict]:
        rows = self._get("/v1/markets").get("markets")
        if not isinstance(rows, list):
            raise ValueError("Arcus response missing markets")
        return [self.normalize_market(row) for row in rows if row.get("status") == "ONLINE"]

    @staticmethod
    def normalize_market(row: dict) -> dict:
        tick = Decimal(row["tickSize"])
        step = Decimal(row["stepSize"])
        minimum = Decimal(row["minOrderSize"])
        if min(tick, step, minimum) <= 0 or not row.get("marketDisplayName"):
            raise ValueError("Arcus market filters are invalid")
        funding_rate = row.get("nextFundingRate")
        if funding_rate is None:
            funding_rate = row.get("fundingRate")
        return {
            "market": row["marketDisplayName"],
            "market_id": int(row["marketId"]),
            "tick_size": tick,
            "size_step": step,
            "min_order_size": minimum,
            "min_order_notional": Decimal(row.get("minOrderNotional") or "0"),
            "maker_fee_rate": Decimal(row.get("effectiveMakerFeePpm") or row.get("baseMakerFeePpm") or "0") / Decimal(1_000_000),
            "taker_fee_rate": Decimal(row.get("effectiveTakerFeePpm") or row.get("baseTakerFeePpm") or "0") / Decimal(1_000_000),
            "volume_24h": Decimal(row.get("volume24hNotional") or "0"),
            "mark_price": Decimal(row["markPrice"]),
            "funding_rate": Decimal(str(funding_rate)) if funding_rate is not None else None,
            "funding_interval_hours": Decimal("1"),
            "next_funding_time": int(row.get("nextFundingAt") or 0),
        }

    def get_bbo(self, market: str) -> dict:
        payload = self._get(f"/v1/bbo/{market}")
        bid, ask = payload.get("bestBid"), payload.get("bestAsk")
        if not bid or not ask:
            raise ValueError("Arcus orderbook has an empty side")
        bid_price, ask_price = Decimal(bid["price"]), Decimal(ask["price"])
        if bid_price <= 0 or bid_price >= ask_price:
            raise ValueError("Arcus orderbook is invalid or crossed")
        return {"bid": bid_price, "ask": ask_price, "bid_size": Decimal(bid["size"]), "ask_size": Decimal(ask["size"])}
