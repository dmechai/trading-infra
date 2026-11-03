"""HIP-3 asset-id helpers for EntropyIO (`io`) markets."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

HIP3_BASE = 100_000
HIP3_DEX_STRIDE = 10_000


def perp_dex_index(perp_dexs: Sequence[Any], dex_name: str) -> int:
    for idx, entry in enumerate(perp_dexs or ()):
        if isinstance(entry, Mapping) and entry.get("name") == dex_name:
            return idx
    raise ValueError(f"DEX {dex_name!r} 不在 perpDexs 中")


def index_in_meta(universe: Sequence[Mapping[str, Any]], coin: str) -> int:
    for idx, asset in enumerate(universe or ()):
        if asset.get("name") == coin:
            return idx
    raise ValueError(f"{coin} 不在 HIP-3 meta universe 中")


def hip3_asset_id(dex_index: int, meta_index: int) -> int:
    return HIP3_BASE + dex_index * HIP3_DEX_STRIDE + meta_index


def resolve_asset_id(perp_dexs: Sequence[Any], meta: Mapping[str, Any], coin: str) -> int:
    dex = coin.split(":", 1)[0] if ":" in coin else ""
    if not dex:
        raise ValueError(f"{coin} 不是 HIP-3 市场")
    universe = meta.get("universe") or []
    return hip3_asset_id(perp_dex_index(perp_dexs, dex), index_in_meta(universe, coin))


def sz_decimals(meta: Mapping[str, Any], coin: str, default: int = 5) -> int:
    for asset in meta.get("universe") or []:
        if asset.get("name") == coin:
            return int(asset.get("szDecimals", default))
    return default
