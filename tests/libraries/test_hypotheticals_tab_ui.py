import importlib
import os
import unittest

import pandas as pd


def _synthetic_normalized_hypo_df(n_symbols=15):
    """15 symbols x 5 dates, with a distinct % change magnitude per symbol so
    top-N truncation has an unambiguous expected winner set."""
    dates = pd.date_range("2026-01-01", periods=5)
    rows = []
    for i in range(n_symbols):
        sym = f"SYM{i:02d}"
        for d in dates:
            rows.append({
                "Date": d,
                "Symbol": sym,
                "Sector": "Tech",
                "ClosingPrice % Change": float(i),
            })
    return pd.DataFrame(rows)


def _plotted_symbols(fig):
    # px.line trace names are "{Symbol}, {Sector}" here (color=Symbol,
    # line_dash=Sector).
    return {tr.name.split(",")[0] for tr in fig.data}


class TestHypotheticalsNoFilterFallback(unittest.TestCase):
    """Covers the GAP 2 fix: clearing both dropdowns must fall back to the
    same top-N-movers default the tab opens with, not render every symbol."""

    @classmethod
    def setUpClass(cls):
        os.environ["PORTFOLIO_DEMO_MODE"] = "1"
        cls.hypotheticals_tab = importlib.import_module(
            "visualization.dash.portfolio_dashboard.tabs.hypotheticals_tab"
        )

    def setUp(self):
        # Bypass _load_hypo_data()'s DASH_HANDLER work entirely by seeding
        # the module-level cache it reads from directly.
        self.df = _synthetic_normalized_hypo_df()
        self.hypotheticals_tab._hypo_cache["normalized_hypo_df"] = self.df

    def test_no_filter_falls_back_to_top_n_not_everything(self):
        fig = self.hypotheticals_tab.update_normalized_hypo_graph(None, None)
        plotted = _plotted_symbols(fig)

        self.assertEqual(len(plotted), 10)  # DEFAULT_CHART_SERIES
        self.assertLess(len(plotted), self.df["Symbol"].nunique())
        # Largest |ClosingPrice % Change| are SYM05..SYM14.
        expected = {f"SYM{i:02d}" for i in range(5, 15)}
        self.assertEqual(plotted, expected)

    def test_explicit_asset_selection_is_not_overridden(self):
        fig = self.hypotheticals_tab.update_normalized_hypo_graph(
            None, ["SYM00", "SYM01"])
        self.assertEqual(_plotted_symbols(fig), {"SYM00", "SYM01"})

    def test_explicit_sector_selection_is_not_overridden(self):
        # A sector filter alone (no explicit assets) should still bypass the
        # top-N fallback -- only an EMPTY selection triggers it.
        fig = self.hypotheticals_tab.update_normalized_hypo_graph(
            ["Tech"], ["SYM03"])
        self.assertEqual(_plotted_symbols(fig), {"SYM03"})


if __name__ == "__main__":
    unittest.main()
