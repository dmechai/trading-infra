"""Entropy market discovery. Default/featured trading choices belong to apps."""
from typing import Any
from ..transport import PublicJSONClient
from ..venues import api_url

MARKETS_PATH = "/api/markets"


def fetch_catalog(*, timeout: float = 8) -> Any:
    return PublicJSONClient(api_url("entropy", "mainnet"), timeout=timeout).get_json(MARKETS_PATH)


def parse_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("entropy.io 市场列表格式无效")
    labels, leverage, isolated = {}, {}, {}
    ordered = []
    for row in payload.get("markets") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name.startswith("io:") or row.get("isDelisted"):
            continue
        if name not in ordered:
            ordered.append(name)
        labels[name] = str(row.get("displayName") or name.split(":", 1)[-1])
        try:
            leverage[name] = max(1, int(float(row.get("maxLeverage") or 10)))
        except (TypeError, ValueError):
            leverage[name] = 10
        isolated[name] = bool(row.get("onlyIsolated", True))
    if not ordered:
        raise ValueError("entropy.io 未返回 io 市场")
    return {"source": "entropy.io", "markets": ordered, "labels": labels,
            "leverage": leverage, "isolated": isolated}
