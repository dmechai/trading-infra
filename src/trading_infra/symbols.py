"""Symbol parsing without stripping contract multipliers or HIP-3 namespaces."""


def native_perp_base(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if "/" in text:
        return text.split("/", 1)[0]
    if ":" in text:
        raise ValueError("Namespaced markets require their exchange-native adapter")
    for suffix in ("-USDT", "-USDC", "-USD", "USDT", "USDC"):
        if text.endswith(suffix):
            return text[:-len(suffix)]
    return text
