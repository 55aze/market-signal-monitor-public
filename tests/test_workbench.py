import unittest
import numpy as np
import pandas as pd
from market_signal_monitor.pine_ops import Series, ema, barssince, nz, lowest, count_condition
from market_signal_monitor.indicator import calculate
from market_signal_monitor.data import normalize
from market_signal_monitor.parity import compare
from market_signal_monitor.curve import curve_closes


def bars(n=800):
    rng = np.random.default_rng(72)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    index = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC", name="time")
    return pd.DataFrame({"open": close, "high": close+1, "low": close-1, "close": close}, index=index)


class Semantics(unittest.TestCase):
    def test_ema_seed_and_recurrence(self):
        np.testing.assert_allclose(ema(Series([2, 4, 8]), 3).v, [2, 3, 5.5])

    def test_barssince_zero_and_prehistory(self):
        np.testing.assert_equal(nz(barssince(Series([0, 0, 1, 0, 0, 1]))).v, [0, 0, 0, 1, 2, 0])

    def test_nested_dynamic_reference_is_not_double_current_offset(self):
        s, offsets = Series([10, 20, 30, 40, 50]), Series([1, 1, 2, 1, 2])
        second = s[offsets]
        np.testing.assert_equal(second.v, [np.nan, 10, 10, 30, 30])
        np.testing.assert_equal(second[offsets].v, [np.nan, np.nan, np.nan, 10, 10])

    def test_na_comparison_and_logical_casts(self):
        condition = Series([np.nan, 1, 0]) > 0
        np.testing.assert_equal(condition.v, [np.nan, 1, 0])
        np.testing.assert_equal((~condition).v, [1, 0, 1])
        np.testing.assert_equal((condition | Series([0, 0, 1])).v, [0, 1, 1])

    def test_extrema_includes_current_bar(self):
        np.testing.assert_equal(lowest(Series([8, 3, 7, 2]), Series([1, 2, 1, 3])).v, [8, 3, 7, 2])

    def test_count_na_cast_and_full_window(self):
        np.testing.assert_equal(count_condition(Series([np.nan, 1, 0, 1]), 2).v, [np.nan, 1, 1, 1])

    def test_history_limit_fails_without_clipping(self):
        with self.assertRaises(ValueError):
            Series([1, 2])[1001]


class IndicatorTests(unittest.TestCase):
    def test_flat_market_no_signal(self):
        data = bars(200)
        data.loc[:, ["open", "high", "low", "close"]] = 10
        out = calculate(data)
        self.assertEqual(out.DXDX.sum() + out.DBJGXC.sum(), 0)
        np.testing.assert_allclose(out.ema200, 10)

    def test_all_outputs_causal(self):
        data = bars()
        full, prefix = calculate(data), calculate(data.iloc[:500])
        pd.testing.assert_frame_equal(full.iloc[:500], prefix)

    def test_positive_scaling_preserves_signals(self):
        data = bars()
        a, b = calculate(data), calculate(data * 10)
        pd.testing.assert_frame_equal(a[["DXDX", "DBJGXC"]], b[["DXDX", "DBJGXC"]])
        self.assertGreater(a.DXDX.sum(), 0)
        self.assertGreater(a.DBJGXC.sum(), 0)

    def test_macd_matches_independent_pandas_ewm(self):
        data = bars()
        out = calculate(data)
        d = data.close.ewm(span=12, adjust=False).mean() - data.close.ewm(span=26, adjust=False).mean()
        np.testing.assert_allclose(out.D, d, atol=1e-12)
        np.testing.assert_allclose(out.A, d.ewm(span=9, adjust=False).mean(), atol=1e-12)


class ParityTests(unittest.TestCase):
    def fixture(self):
        ref = bars(10)
        ref["DXDX"] = [0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        ref["DBJGXC"] = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0]
        ref["bar_confirmed"] = 1
        return ref

    def test_exact_metrics(self):
        ref = self.fixture()
        out = compare(ref, ref.copy(), 0)
        self.assertEqual(out["status"], "match_on_supplied_sample")
        self.assertEqual(out["signals"]["DXDX"]["precision_exact"], 1)

    def test_shift_diagnostic_does_not_count_as_exact(self):
        ref = self.fixture()
        cand = ref.copy()
        cand["DXDX"] = cand.DXDX.shift(1).fillna(0)
        out = compare(ref, cand, 0)
        self.assertEqual(out["status"], "mismatch")
        self.assertEqual(out["signals"]["DXDX"]["precision_exact"], 0)
        self.assertEqual(len(out["signals"]["DXDX"]["offset_diagnostics_only"]), 2)

    def test_dependency_diagnostic_finds_first_chain_divergence(self):
        ref = self.fixture()
        for col in ["D", "A", "M", "N1", "MM1", "CC1", "CC2", "CC3", "DIFL1", "DIFL2",
                    "DIFL3", "AAA", "BBB", "CCC", "JJJ"]:
            ref[col] = 0.0
        cand = ref.copy()
        t = ref.index[2]
        cand.loc[t, "DXDX"] = 0
        cand.loc[t, "CCC"] = 1
        cand.loc[t, "JJJ"] = 1
        out = compare(ref, cand, 0)
        diagnostic = out["dependency_diagnostics"]["DXDX"][0]
        self.assertEqual(diagnostic["time"], str(t))
        self.assertEqual(diagnostic["first_divergence"]["column"], "CCC")
        self.assertEqual(diagnostic["first_divergence"]["tv"], 0.0)
        self.assertEqual(diagnostic["first_divergence"]["python"], 1.0)

    def test_missing_event_bar_not_hidden_by_inner_join(self):
        ref = self.fixture()
        out = compare(ref, ref.drop(ref.index[2]), 0)
        self.assertEqual(len(out["missing_candidate_times"]), 1)
        self.assertEqual(out["signals"]["DXDX"]["recall_exact"], .5)

    def test_no_events_not_pass(self):
        ref = self.fixture()
        ref[["DXDX", "DBJGXC"]] = 0
        out = compare(ref, ref.copy(), 0)
        self.assertEqual(out["status"], "inconclusive_no_events")
        self.assertIsNone(out["signals"]["DXDX"]["precision_exact"])

    def test_unconfirmed_bar_ignored(self):
        ref = self.fixture()
        ref.loc[ref.index[-1], "bar_confirmed"] = 0
        cand = ref.copy()
        cand.loc[cand.index[-1], "DXDX"] = 1
        self.assertEqual(compare(ref, cand, 0)["status"], "match_on_supplied_sample")

    def test_price_difference_fails_even_when_signals_match(self):
        ref = self.fixture()
        cand = ref.copy()
        cand["close"] += .01
        self.assertEqual(compare(ref, cand, 0)["status"], "mismatch")


class InputAndCurveTests(unittest.TestCase):
    def test_duplicate_and_naive_times_rejected(self):
        for times in (["2026-01-01", "2026-01-02"], ["2026-01-01T00:00Z"] * 2):
            frame = pd.DataFrame({"time": times, "open": 1, "high": 2, "low": 0, "close": 1})
            with self.assertRaises(ValueError): normalize(frame)

    def test_curve_units_no_stale_fill_no_fake_high(self):
        idx = pd.date_range("2026-01-01", periods=2, tz="UTC")
        yields = {k: pd.Series(v, index=idx) for k, v in
                  {"US02Y": [4, 4], "US05Y": [4.1, np.nan], "US10Y": [4.2, 4.3], "US30Y": [4.5, 4.6]}.items()}
        out = curve_closes(yields)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out["2s30s_bp"].iloc[0], 50)
        self.assertAlmostEqual(out["2s10s_bp"].iloc[0], 20)
        self.assertNotIn("high", out)


if __name__ == "__main__": unittest.main()
