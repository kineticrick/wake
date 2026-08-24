import unittest
from unittest import mock

from libraries.globals import ReadOnlyModeError
from libraries.yfinance_helpers import yfinancelib


class TestGetHistoricalPricesEmptyBatch(unittest.TestCase):
    """yfinance returns empty data for every symbol when the requested window
    has no completed trading sessions (e.g. a market-holiday + weekend gap, as
    happens the Monday after Juneteenth).

    Previously that produced a frame with only a 'Date' column, which crashed
    far away in gen_assets_historical_value with a cryptic ``KeyError: 'Symbol'``.
    The price layer should instead return a correctly-shaped empty frame so the
    caller's merge is a clean no-op (no new history added until real data exists).
    """

    def test_all_symbols_empty_returns_shaped_empty_frame(self):
        with mock.patch.object(
                yfinancelib, "_gen_historical_prices",
                return_value={"QQQ": {}, "FATE": {}}):
            df = yfinancelib.get_historical_prices(
                ["QQQ", "FATE"], start="2026-06-19", end="2026-06-22",
                interval="daily", cleaned_up=True)
        # Empty, but with the columns the downstream merge-on-['Date','Symbol']
        # needs — so it's a no-op, not a KeyError.
        self.assertTrue(df.empty)
        self.assertIn("Date", df.columns)
        self.assertIn("Symbol", df.columns)
        self.assertIn("ClosingPrice", df.columns)

    def test_partial_empty_still_returns_good_symbols(self):
        # One real symbol, one empty -> we keep the good one, no raise.
        good = {"2026-06-12": {"Close": 100.0}, "2026-06-13": {"Close": 101.0}}
        with mock.patch.object(
                yfinancelib, "_gen_historical_prices",
                return_value={"AAA": good, "FATE": {}}):
            df = yfinancelib.get_historical_prices(
                ["AAA", "FATE"], start="2026-06-10", end="2026-06-14",
                interval="daily", cleaned_up=True)
        self.assertIn("Symbol", df.columns)
        self.assertEqual(set(df["Symbol"].unique()), {"AAA"})


class TestGetHistoricalPricesReadOnlyGate(unittest.TestCase):
    """
    CRITICAL 1: get_historical_prices is the one price-fetch path NOT gated
    by a history handler's read_only flag -- the chat layer's account-filtered
    breakdown calls it directly (via gen_assets_historical_value), bypassing
    BaseHistoryHandler entirely. Under PORTFOLIO_READ_ONLY=1 it must raise
    immediately, before touching yfinance, rather than performing a
    multi-ticker live fetch (measured at 65+ seconds for a full portfolio).
    """

    def test_raises_without_calling_yfinance(self):
        def explode(*a, **kw):
            raise AssertionError(
                "yfinance must not be called when PORTFOLIO_READ_ONLY=1")

        with mock.patch.object(yfinancelib, 'PORTFOLIO_READ_ONLY', True), \
             mock.patch.object(yfinancelib, '_gen_historical_prices',
                               side_effect=explode):
            with self.assertRaises(ReadOnlyModeError):
                yfinancelib.get_historical_prices(
                    ['AAPL'], start='2026-01-01', end='2026-01-10',
                    interval='daily')

    def test_write_mode_is_unaffected(self):
        good = {"2026-06-12": {"Close": 100.0}, "2026-06-13": {"Close": 101.0}}
        with mock.patch.object(yfinancelib, 'PORTFOLIO_READ_ONLY', False), \
             mock.patch.object(yfinancelib, '_gen_historical_prices',
                               return_value={"AAA": good}):
            df = yfinancelib.get_historical_prices(
                ["AAA"], start="2026-06-10", end="2026-06-14",
                interval="daily", cleaned_up=True)
        self.assertFalse(df.empty)


if __name__ == "__main__":
    unittest.main()
