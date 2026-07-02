import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "market_snapshot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("market_snapshot", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["market_snapshot"] = module
    spec.loader.exec_module(module)
    return module


class MarketSnapshotTest(unittest.TestCase):
    def test_builds_tencent_batch_symbols(self):
        market_snapshot = load_module()

        holdings = [
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
            {"code": "512480", "name": "半导体ETF"},
        ]

        self.assertEqual(
            market_snapshot.build_tencent_symbols(holdings),
            ["sh600519", "sz000001", "sh512480"],
        )

    def test_ttl_cache_reuses_fresh_fetch(self):
        market_snapshot = load_module()
        calls = []
        cache = market_snapshot.TTLCache(ttl_sec=30)

        def fetcher():
            calls.append(time.time())
            return {"value": len(calls)}

        first = cache.get_or_fetch("quote:600519", fetcher)
        second = cache.get_or_fetch("quote:600519", fetcher)

        self.assertEqual(first, {"value": 1})
        self.assertEqual(second, {"value": 1})
        self.assertEqual(len(calls), 1)

    def test_refresh_final_quotes_updates_timestamp_and_quote_values(self):
        market_snapshot = load_module()
        snapshot = {
            "timestamp": "2026-06-24T10:00:00+08:00",
            "holdings": [
                {"code": "600519", "quote": {"price": 1680.0, "quote_time": "10:00:00"}},
                {"code": "000001", "quote": {"price": 10.0, "quote_time": "10:00:00"}},
            ],
        }

        def quote_fetcher(holdings):
            return {
                "600519": {"price": 1691.0, "quote_time": "10:07:21", "source": "fake"},
                "000001": {"price": 10.2, "quote_time": "10:07:21", "source": "fake"},
            }

        refreshed = market_snapshot.refresh_final_quotes(snapshot, quote_fetcher)

        self.assertIn("final_quote_refresh_at", refreshed)
        self.assertEqual(refreshed["holdings"][0]["quote"]["price"], 1691.0)
        self.assertEqual(refreshed["holdings"][1]["quote"]["price"], 10.2)

    def test_decodes_tencent_response_bytes_as_utf8_or_gbk(self):
        market_snapshot = load_module()

        utf8_payload = 'v_sh600519="1~贵州茅台~600519~1207.68~1222.45~1222.65";'.encode("utf-8")
        gbk_payload = 'v_sh600519="1~贵州茅台~600519~1207.68~1222.45~1222.65";'.encode("gbk")

        self.assertIn("贵州茅台", market_snapshot.decode_tencent_bytes(utf8_payload))
        self.assertIn("贵州茅台", market_snapshot.decode_tencent_bytes(gbk_payload))

    def test_cli_can_emit_snapshot_from_holdings_file_without_network(self):
        market_snapshot = load_module()
        tmp = Path(__file__).with_suffix(".holdings.json")
        try:
            tmp.write_text(json.dumps([{"code": "600519", "name": "贵州茅台"}], ensure_ascii=False), encoding="utf-8")
            result = market_snapshot.build_empty_snapshot(market_snapshot.load_holdings(tmp))
            self.assertEqual(result["holdings"][0]["code"], "600519")
            self.assertEqual(result["holdings"][0]["quote"]["source"], "[数据缺失: quote]")
        finally:
            tmp.unlink(missing_ok=True)

    def test_eastmoney_requests_go_through_throttled_session(self):
        market_snapshot = load_module()
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                calls.append({"url": url, "params": params, "timeout": timeout})
                return FakeResponse()

        original_session = market_snapshot._SESSION
        original_interval = market_snapshot.EM_MIN_INTERVAL_SEC
        original_uniform = market_snapshot.random.uniform
        try:
            market_snapshot._SESSION = FakeSession()
            market_snapshot.EM_MIN_INTERVAL_SEC = 0
            market_snapshot.random.uniform = lambda _a, _b: 0
            market_snapshot._em_get("https://push2.eastmoney.com/api/test", params={"x": "1"}, timeout=3)
        finally:
            market_snapshot._SESSION = original_session
            market_snapshot.EM_MIN_INTERVAL_SEC = original_interval
            market_snapshot.random.uniform = original_uniform

        self.assertEqual(calls[0]["url"], "https://push2.eastmoney.com/api/test")
        self.assertEqual(calls[0]["params"], {"x": "1"})
        self.assertEqual(calls[0]["timeout"], 3)

    def test_baidu_concept_route_does_not_use_removed_fundflow_endpoint(self):
        market_snapshot = load_module()
        calls = []

        class FakeResponse:
            def json(self):
                return {"Result": {"concepts": ["人工智能", "算力"]}}

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                calls.append({"url": url, "params": params})
                return FakeResponse()

        original_session = market_snapshot._SESSION
        try:
            market_snapshot._SESSION = FakeSession()
            result = market_snapshot.fetch_concept_blocks("600519", timeout_sec=1)
        finally:
            market_snapshot._SESSION = original_session

        self.assertEqual(result["items"], ["人工智能", "算力"])
        self.assertNotIn("fundflow", calls[0]["url"])
        self.assertNotIn("fundsortlist", calls[0]["url"])

    def test_snapshot_records_quote_source_chain_and_missing_fields(self):
        market_snapshot = load_module()
        holdings = [{"code": "600519", "name": "贵州茅台"}, {"code": "000001", "name": "平安银行"}]

        def quote_fetcher(_holdings):
            return {
                "quotes": {
                    "600519": {"code": "600519", "price": 1200.0, "source": "Sina hq.sinajs.cn"},
                },
                "source_chains": {
                    "600519": ["Tencent qt.gtimg.cn", "Sina hq.sinajs.cn"],
                    "000001": ["Tencent qt.gtimg.cn", "Sina hq.sinajs.cn", "Eastmoney push2"],
                },
                "errors": ["Tencent quote failed"],
            }

        def context_fetcher():
            return ({"major_indices": [{"code": "000001"}], "hot_sectors": [], "northbound": {}, "news": []}, ["market.hot_sectors"], [])

        def holding_enricher(holding):
            holding["data_quality"] = market_snapshot.grade_holding_quality(holding)
            return holding, [], []

        snapshot = market_snapshot.build_snapshot(holdings, quote_fetcher, context_fetcher, holding_enricher)

        self.assertEqual(snapshot["holdings"][0]["quote"]["source_chain"], ["Tencent qt.gtimg.cn", "Sina hq.sinajs.cn"])
        self.assertEqual(snapshot["holdings"][1]["quote"]["source"], "[数据缺失: quote]")
        self.assertIn("quote:000001", snapshot["missing_fields"])
        self.assertIn("Tencent quote failed", snapshot["errors"])

    def test_quality_gate_blocks_actions_without_quote_and_new_buys_without_sector(self):
        market_snapshot = load_module()
        no_quote = {
            "holdings": [{"data_quality": "F"}],
            "missing_fields": ["quote:600519", "market.hot_sectors"],
        }
        blocked = market_snapshot.assess_quality(no_quote)
        self.assertEqual(blocked["grade"], "F")
        self.assertFalse(blocked["action_allowed"])
        self.assertFalse(blocked["new_buy_allowed"])

        no_sector = {
            "holdings": [{"data_quality": "A"}],
            "missing_fields": ["market.hot_sectors"],
        }
        sector_blocked = market_snapshot.assess_quality(no_sector)
        self.assertTrue(sector_blocked["action_allowed"])
        self.assertFalse(sector_blocked["new_buy_allowed"])

    def test_eastmoney_news_fallback_uses_sort_end_zero(self):
        market_snapshot = load_module()
        calls = []

        class FakeResponse:
            def __init__(self, payload=None, should_raise=False):
                self.payload = payload or {}
                self.should_raise = should_raise

            def json(self):
                if self.should_raise:
                    raise RuntimeError("CLS unavailable")
                return self.payload

            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                calls.append({"url": url, "params": params})
                if "cls.cn" in url:
                    return FakeResponse(should_raise=True)
                return FakeResponse(
                    {
                        "code": "1",
                        "message": "success",
                        "data": {
                            "fastNewsList": [
                                {"title": "贵金属概念走强", "showTime": "2026-07-02 10:38:26"},
                            ],
                        },
                    }
                )

        original_session = market_snapshot._SESSION
        original_interval = market_snapshot.EM_MIN_INTERVAL_SEC
        original_uniform = market_snapshot.random.uniform
        try:
            market_snapshot._SESSION = FakeSession()
            market_snapshot.EM_MIN_INTERVAL_SEC = 0
            market_snapshot.random.uniform = lambda _a, _b: 0
            result = market_snapshot.fetch_market_news(1)
        finally:
            market_snapshot._SESSION = original_session
            market_snapshot.EM_MIN_INTERVAL_SEC = original_interval
            market_snapshot.random.uniform = original_uniform

        self.assertEqual(result, [{"title": "贵金属概念走强", "source": "Eastmoney 7x24", "time": "2026-07-02 10:38:26"}])
        self.assertEqual(calls[-1]["params"]["sortEnd"], "0")

    def test_eastmoney_news_parameter_error_is_recorded_as_market_news_missing(self):
        market_snapshot = load_module()

        def quote_fetcher(_holdings):
            return {"600519": {"code": "600519", "price": 1200.0, "source": "fake"}}

        def holding_enricher(holding):
            holding["data_quality"] = market_snapshot.grade_holding_quality(holding)
            return holding, [], []

        original_indices = market_snapshot._fetch_tencent_symbol_list
        original_hot = market_snapshot.fetch_hot_sectors
        original_northbound = market_snapshot.fetch_northbound_flow
        original_news = market_snapshot.fetch_market_news
        try:
            market_snapshot._fetch_tencent_symbol_list = lambda _symbols, _timeout: {"000001": {"code": "000001"}}
            market_snapshot.fetch_hot_sectors = lambda _timeout: [{"name": "贵金属"}]
            market_snapshot.fetch_northbound_flow = lambda _timeout: {"source": "fake", "history": [{"date": "2026-07-02"}]}
            market_snapshot.fetch_market_news = lambda _timeout: (_ for _ in ()).throw(
                ValueError("Eastmoney 7x24 news failed: Required String parameter 'sortEnd' is not present")
            )

            snapshot = market_snapshot.build_snapshot(
                [{"code": "600519", "name": "贵州茅台"}],
                quote_fetcher,
                lambda: market_snapshot.fetch_market_context(1),
                holding_enricher,
            )
        finally:
            market_snapshot._fetch_tencent_symbol_list = original_indices
            market_snapshot.fetch_hot_sectors = original_hot
            market_snapshot.fetch_northbound_flow = original_northbound
            market_snapshot.fetch_market_news = original_news

        self.assertIn("market.news", snapshot["missing_fields"])
        self.assertTrue(any("Required String parameter 'sortEnd'" in error for error in snapshot["errors"]))

    def test_eastmoney_news_data_null_raises_diagnostic_error(self):
        market_snapshot = load_module()

        class FakeResponse:
            def __init__(self, payload=None, should_raise=False):
                self.payload = payload or {}
                self.should_raise = should_raise

            def json(self):
                if self.should_raise:
                    raise RuntimeError("CLS unavailable")
                return self.payload

            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                if "cls.cn" in url:
                    return FakeResponse(should_raise=True)
                return FakeResponse({"code": 0, "message": "Required String parameter 'sortEnd' is not present", "data": None})

        original_session = market_snapshot._SESSION
        original_interval = market_snapshot.EM_MIN_INTERVAL_SEC
        original_uniform = market_snapshot.random.uniform
        try:
            market_snapshot._SESSION = FakeSession()
            market_snapshot.EM_MIN_INTERVAL_SEC = 0
            market_snapshot.random.uniform = lambda _a, _b: 0
            with self.assertRaisesRegex(ValueError, "sortEnd"):
                market_snapshot.fetch_market_news(1)
        finally:
            market_snapshot._SESSION = original_session
            market_snapshot.EM_MIN_INTERVAL_SEC = original_interval
            market_snapshot.random.uniform = original_uniform

    def test_trading_rules_make_loss_an_evidence_input_not_an_auto_reduce_trigger(self):
        rules = (SCRIPT_PATH.parents[1] / "references" / "trading-rules.md").read_text(encoding="utf-8")

        self.assertIn("亏损不是减仓的充分条件", rules)
        self.assertNotIn("Heavy loser underperforms index/sector | Reduce;", rules)


if __name__ == "__main__":
    unittest.main()
