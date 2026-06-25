"""Y-axis tick labels: XAUT (~4k) and BTC (~70k) scales."""
import unittest

from xauby.ui import chart_axis as _chart_axis


class ChartPriceAxisTest(unittest.TestCase):
    def _labels(self, mn: float, mx: float, height: int) -> list:
        step = _chart_axis.nice_price_step(mx - mn)
        rows = _chart_axis.price_axis_label_rows(mn, mx, height, step)
        return [
            _chart_axis.format_price_axis(rows[r], step)
            for r in sorted(rows.keys(), reverse=True)
        ]

    def test_xaut_tight_range_clean_ticks(self):
        labels = self._labels(4338.5, 4362.0, 8)
        self.assertEqual(
            labels,
            ["$4,360", "$4,355", "$4,350", "$4,345", "$4,340"],
        )
        for label in labels:
            self.assertTrue(label.startswith("$4,"))
            self.assertNotIn(".", label)  # whole dollars for XAUT

    def test_xaut_narrow_intraday_step(self):
        step = _chart_axis.nice_price_step(10.0)
        self.assertEqual(step, 2.0)
        rows = _chart_axis.price_axis_label_rows(4345.0, 4355.0, 6, step)
        self.assertEqual(rows[2], 4350.0)
        self.assertEqual(_chart_axis.format_price_axis(4350.0, step), "$4,350")

    def test_btc_range_clean_ticks(self):
        labels = self._labels(73106.0, 74591.0, 6)
        self.assertEqual(labels, ["$74,200", "$74,000", "$73,600", "$73,400", "$73,200"])

    def test_no_row_interpolation_leak(self):
        rows = _chart_axis.price_axis_label_rows(73106.0, 74591.0, 6, 500.0)
        for price in rows.values():
            self.assertEqual(price % 500, 0)

    def test_micro_price_clean_ticks(self):
        # Range of 0.0007, nice_price_step gives 0.0001
        labels = self._labels(0.0021, 0.0028, 8)
        self.assertEqual(
            labels,
            ["$0.0028", "$0.0027", "$0.0026", "$0.0025", "$0.0024", "$0.0023", "$0.0022", "$0.0021"],
        )


if __name__ == "__main__":
    unittest.main()
