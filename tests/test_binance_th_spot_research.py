import unittest

from scripts import btc_supertrend_okx_pf_grid as btc
from scripts import certify_preset
from scripts import xau_okx_pf_grid as xau
from xauby.saas.catalog import EXCHANGE_PROFILES


class BinanceThSpotResearchTests(unittest.TestCase):
    def test_grid_is_long_only_and_has_pre_registered_trial_counts(self):
        btc_items = [item for item in btc.grid_items() if item["variant"].startswith("long-only")]
        xaut_items = [item for item in xau.grid_items() if item["variant"].startswith("long-only")]
        self.assertEqual(len(btc_items), 192)
        self.assertEqual(len(xaut_items), 144)
        self.assertTrue(all(not item["override"]["enable_short"] for item in btc_items + xaut_items))
        self.assertEqual({item["variant"] for item in btc_items}, {"long-only D1 off", "long-only D1 on"})
        self.assertEqual({item["variant"] for item in xaut_items}, {"long-only D1 off", "long-only D1 on"})

    def test_certification_sources_are_native_and_cost_aligned(self):
        fee = EXCHANGE_PROFILES["binance-th-spot-usdt"]["exchange"]["fee_pct"]
        for preset_id in ("binance-th-btcusdt-supertrend-v1", "binance-th-xautusdt-actionzone-v1"):
            source = certify_preset.DATA_SOURCES[preset_id]
            self.assertEqual(source["venue"], "binance_th")
            self.assertEqual(source["base_url"], "https://api.binance.th")
            self.assertTrue(source["native"])
            self.assertEqual(source["fee_pct"], fee)

    def test_xaut_proxy_is_explicitly_non_native(self):
        proxy = certify_preset.DATA_SOURCES["binance-th-xautusdt-actionzone-v1"]["proxy"]
        self.assertEqual(proxy["symbol"], "PAXGUSDT")
        self.assertFalse(proxy["native"])


if __name__ == "__main__":
    unittest.main()
