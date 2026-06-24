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


if __name__ == "__main__":
    unittest.main()
