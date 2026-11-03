"""Public CCXT factory; authenticated execution clients remain application-owned."""
from ..venues import BINANCE_PUBLIC_ROUTES


def create_public_exchange(exchange_id: str, market_type: str = "swap", *,
                           timeout_ms: int = 30000, proxy_url: str | None = None,
                           ccxt_module=None):
    if market_type not in {"swap", "spot"}:
        raise ValueError(f"Unsupported market type: {market_type}")
    if ccxt_module is None:
        import ccxt.async_support as ccxt_module
    options = {"defaultType": "spot" if market_type == "spot" else "swap"}
    if exchange_id == "binance":
        options["defaultType"] = "future" if market_type == "swap" else "spot"
        options["fetchMarkets"] = ["linear" if market_type == "swap" else "spot"]
    cfg = {
        "apiKey": "", "secret": "", "enableRateLimit": True,
        "timeout": timeout_ms, "options": options,
    }
    if proxy_url:
        cfg["aiohttp_proxy"] = proxy_url
        cfg["proxies"] = {"http": proxy_url, "https": proxy_url}
    exchange = getattr(ccxt_module, exchange_id)(cfg)
    if exchange_id == "binance":
        exchange.urls["api"] = {**(exchange.urls.get("api") or {}), **BINANCE_PUBLIC_ROUTES}
    return exchange
