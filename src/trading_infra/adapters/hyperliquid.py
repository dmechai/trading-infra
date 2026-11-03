"""Shared Hyperliquid public payloads and SDK compatibility (no account access)."""
from typing import Any


def asset_contexts(payload: Any) -> dict[str, dict]:
    if not isinstance(payload, (list, tuple)) or len(payload) < 2 or not isinstance(payload[0], dict):
        return {}
    contexts = payload[1] if isinstance(payload[1], (list, tuple)) else []
    result = {}
    for asset, ctx in zip(payload[0].get("universe") or [], contexts):
        if isinstance(asset, dict) and asset.get("name") and isinstance(ctx, dict):
            result[str(asset["name"])] = {"asset": asset, "ctx": ctx}
    return result


def read_order_book(info: Any, coin: str, dex: str | None, post_json) -> dict:
    """Read an SDK L2 snapshot, handling SDK versions without a dex argument."""
    snapshot = getattr(info, "l2_snapshot", None)
    if not callable(snapshot):
        raise RuntimeError("Hyperliquid SDK 不支持 l2_snapshot，盘口暂不可用")
    raw = None
    try:
        raw = snapshot(coin, dex=dex) if dex else snapshot(coin)
    except TypeError:
        try:
            raw = snapshot(coin=coin, dex=dex) if dex else snapshot(coin=coin)
        except TypeError:
            if dex:
                try:
                    raw = snapshot(coin)
                except Exception:
                    raw = None
            else:
                raise
    if not isinstance(raw, dict) or not raw.get("levels"):
        try:
            posted = post_json({"type": "l2Book", "coin": coin})
            if isinstance(posted, dict):
                raw = posted
        except Exception:
            if raw is None:
                raise
    if not isinstance(raw, dict):
        raise RuntimeError(f"无法读取 {coin} 盘口")
    return raw
