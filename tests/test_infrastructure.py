"""Offline boundary tests: no exchange access, credentials, or order writes."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from trading_infra.adapters.ccxt import create_public_exchange
from trading_infra.adapters.hyperliquid import asset_contexts, read_order_book
from trading_infra.adapters.risex import RiseXPublicClient
from trading_infra.adapters.arcus import ArcusPublicClient
from trading_infra.funding import parse_interval_hours
from trading_infra.market_data import CexMarketData
from trading_infra.models import QuoteSnapshot
from trading_infra.symbols import native_perp_base
from trading_infra.transport import PublicJSONClient
from trading_infra.venues import api_url

class InfrastructureTests(unittest.TestCase):
    def test_public_factory_keeps_private_routes_and_has_no_credentials(self):
        client = SimpleNamespace(urls={"api": {"private": "original", "fapiPrivate": "private-futures"}})
        factory = Mock(return_value=client)
        result = create_public_exchange("binance", timeout_ms=9000, proxy_url="http://proxy",
                                        ccxt_module=SimpleNamespace(binance=factory))
        cfg = factory.call_args.args[0]
        self.assertEqual((cfg["apiKey"], cfg["secret"]), ("", ""))
        self.assertEqual(cfg["options"], {"defaultType": "future", "fetchMarkets": ["linear"]})
        self.assertEqual(cfg["timeout"], 9000)
        self.assertEqual(cfg["aiohttp_proxy"], "http://proxy")
        self.assertEqual(result.urls["api"]["private"], "original")
        self.assertEqual(result.urls["api"]["fapiPrivate"], "private-futures")
        self.assertIn("binance.vision", result.urls["api"]["public"])

    def test_networks_are_explicit_and_independent(self):
        for cls in (RiseXPublicClient, ArcusPublicClient):
            self.assertNotEqual(cls("mainnet").api_url, cls("testnet").api_url)
            with self.assertRaises(ValueError):
                cls("live")
        with self.assertRaises(ValueError):
            api_url("lighter", "testnet")
        with self.assertRaises(ValueError):
            PublicJSONClient("https://example.invalid", timeout=0)

    def test_funding_units(self):
        for value in ("8h", "480m", 480, 28800, 28800000):
            self.assertEqual(parse_interval_hours(value), 8)
        self.assertEqual(parse_interval_hours("0.5h"), .5)
        for value in (None, "", "bad", 0, -1):
            self.assertIsNone(parse_interval_hours(value))

    def test_symbols_preserve_contract_identity(self):
        for value in ("BTC", "BTC-USDT", "BTC-USD", "BTC/USDC:USDC"):
            self.assertEqual(native_perp_base(value), "BTC")
        self.assertEqual(native_perp_base("1000PEPE/USDT:USDT"), "1000PEPE")
        with self.assertRaises(ValueError):
            native_perp_base("io:ANTH")

    def test_sdk_fallback_preserves_hip3_coin(self):
        raw = {"levels": [[{"px": "99"}], [{"px": "101"}]]}
        info = SimpleNamespace(l2_snapshot=Mock(side_effect=[TypeError(), TypeError(), raw]))
        post = Mock()
        self.assertIs(read_order_book(info, "io:ANTH", "io", post), raw)
        post.assert_not_called()
        self.assertEqual(info.l2_snapshot.call_args.args, ("io:ANTH",))
        info.l2_snapshot = Mock(return_value=None)
        post.return_value = raw
        self.assertIs(read_order_book(info, "io:ANTH", "io", post), raw)
        post.assert_called_once_with({"type": "l2Book", "coin": "io:ANTH"})

    def test_contexts_do_not_reassign_missing_rows(self):
        payload = [{"universe": [{"name": "BTC"}, {"name": "ETH"}]}, [{"funding": ".001"}]]
        self.assertEqual(list(asset_contexts(payload)), ["BTC"])
        self.assertEqual(asset_contexts({}), {})

    def test_quote_freshness_keeps_original_receive_time(self):
        quote = QuoteSnapshot("hyperliquid", "BTC", "BTC", 99, 101, received_at=100)
        self.assertTrue(quote.is_fresh(3, now=102))
        self.assertFalse(quote.is_fresh(3, now=104))
        self.assertEqual(quote.as_dict()["received_at"], 100)


class AsyncInfrastructureTests(unittest.IsolatedAsyncioTestCase):
    async def test_cex_connections_are_reused_and_released(self):
        client = SimpleNamespace(
            markets={"BTC/USDT:USDT": {"contractSize": 1}},
            fetch_ticker=AsyncMock(return_value={"quoteVolume": 123}),
            fetch_order_book=AsyncMock(return_value={"bids": [[99, 2]], "asks": [[101, 3]], "timestamp": 5000}),
            fetch_funding_rate=AsyncMock(return_value={"fundingRate": .001, "interval": "480m"}),
            close=AsyncMock(),
        )
        factory = Mock(return_value=client)
        service = CexMarketData(factory)
        rows = await asyncio.gather(service.quote("bitget", "BTC"), service.quote("bitget", "BTC"))
        factory.assert_called_once_with("bitget", "swap")
        self.assertEqual(rows[0]["interval_hours"], 8)
        self.assertEqual(rows[0]["bid_notional"], 198)
        self.assertEqual(rows[0]["source_timestamp"], 5)
        client.fetch_funding_rate.return_value = {}
        row = await service.quote("bitget", "BTC")
        self.assertIsNone(row["funding_rate"])
        await service.close()
        await service.close()
        client.close.assert_awaited_once()

    async def test_dex_quotes_cache_and_clear_on_close(self):
        from trading_infra import dex
        await dex.close()
        ctx = [{"universe": [{"name": "BTC"}]}, [{"funding": ".001", "markPx": "100"}]]
        book = {"levels": [[{"px": "99", "sz": "2"}], [{"px": "101", "sz": "3"}]]}
        with patch.object(dex, "_post_json", new=AsyncMock(side_effect=[ctx, book])) as post:
            first = await dex.quote("hyperliquid", "BTC/USDC:USDC")
            second = await dex.quote("hyperliquid", "BTC")
        self.assertEqual(first, second)
        self.assertEqual(first["interval_hours"], 1)
        self.assertEqual(post.await_count, 2)
        await dex.close()
        self.assertEqual(dex._cache, {})

    async def test_scan_retains_successful_venues(self):
        from trading_infra import dex
        original = dict(dex.QUOTE_ADAPTERS)
        good = AsyncMock(return_value={"base": "BTC", "exchange": "good"})
        bad = AsyncMock(side_effect=TimeoutError("offline"))
        try:
            dex.register_quote_adapter("good", good)
            dex.register_quote_adapter("bad", bad)
            rows = await dex.scan_quotes(["BTC"], exchanges=("good", "bad"))
            self.assertEqual(len(rows), 1)
            with self.assertRaises(RuntimeError):
                await dex.scan_quotes(["BTC"], exchanges=("bad",))
        finally:
            dex.QUOTE_ADAPTERS.clear()
            dex.QUOTE_ADAPTERS.update(original)
