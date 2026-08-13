"""Test doubles for chat tools and engine — no DB, no network, deterministic."""

import pandas as pd

from libraries.chat.provider import LLMResponse

# Fixture dates are anchored to "today" rather than hardcoded. The windowed
# intervals ("6m" -> today - 180d, etc.) filter against real wall-clock time,
# so fixed dates silently drift out of the window as the calendar advances and
# stop exercising the path under test — a windowed breakdown then collapses to
# a single row per group and reports a 0% return instead of the real one.
_TODAY = pd.Timestamp.today().normalize()


def _days_ago(n):
    return (_TODAY - pd.Timedelta(days=n)).date()


# Comfortably inside the 180-day "6m" window, with headroom on both ends.
_WINDOW_OPEN = _days_ago(150)
_WINDOW_MID = _days_ago(75)
_WINDOW_CLOSE = _days_ago(5)


def make_fake_handler():
    """A stand-in for DashboardHandler with just enough data for the tools."""

    class FakeHandler:
        performance_milestones = [
            ("1d", 1), ("1w", 7), ("1m", 30), ("3m", 90), ("6m", 180),
            ("1y", 365), ("2y", 730), ("3y", 1095), ("5y", 1825),
        ]

        current_portfolio_summary_df = pd.DataFrame({
            "Symbol": ["AAA", "BBB", "CCC"],
            "Name": ["Alpha", "Beta", "Gamma"],
            "Sector": ["Tech", "Tech", "Health"],
            "AssetType": ["Common Stock", "ETF", "Common Stock"],
            "AccountType": ["Discretionary", "Retirement", "Discretionary"],
            "Geography": ["US", "US", "ex-US"],
            "Current Price": [10.0, 20.0, 5.0],
            "Current Value": [1000.0, 2000.0, 500.0],
            "Cost Basis": [800.0, 2200.0, 400.0],
            "Lifetime Return": [25.0, -9.09, 25.0],
            "Dividend Yield": [1.0, 2.0, 0.0],
            "Total Dividend": [10.0, 40.0, 0.0],
        })

        current_portfolio_value = 3500.0

        # Pre-ranked per-interval asset returns (Symbol, Interval, Current Price,
        # Price, Price % Return) — matches get_ranked_assets output columns.
        _asset_milestones = pd.DataFrame({
            "Symbol": ["AAA", "BBB", "CCC", "AAA", "BBB", "CCC"],
            "Interval": ["6m", "6m", "6m", "1y", "1y", "1y"],
            "Current Price": [10.0, 20.0, 5.0, 10.0, 20.0, 5.0],
            "Price": [8.0, 25.0, 4.0, 6.0, 30.0, 3.0],
            "Price % Return": [25.0, -20.0, 25.0, 66.7, -33.3, 66.7],
            "Value % Return": [25.0, -20.0, 25.0, 66.7, -33.3, 66.7],
        })

        def get_ranked_assets(self, interval, price_or_value="price",
                              ascending=False, count=None):
            col = "Price % Return" if price_or_value == "price" else "Value % Return"
            df = self._asset_milestones
            df = df[df["Interval"] == interval].sort_values(col, ascending=ascending)
            if count:
                df = df.head(count)
            return df

        def get_portfolio_milestones(self):
            return pd.DataFrame({
                "Date": pd.to_datetime([_WINDOW_MID, _WINDOW_CLOSE]),
                "Interval": ["6m", "Lifetime"],
                "Value": [3000.0, 3500.0],
                "Value % Return": [16.67, 12.9],
            })

        def get_asset_milestones(self, symbols=None):
            df = self._asset_milestones
            if symbols:
                df = df[df["Symbol"].isin(symbols)]
            return df

        # Dimension summary (Lifetime VW Return lives here).
        sectors_summary_df = pd.DataFrame({
            "Sector": ["Tech", "Health"],
            "Current Value": [3000.0, 500.0],
            "Cost Basis": [3000.0, 400.0],
            "VW Return": [0.0, 25.0],
        })

        # Dimension history (windowed VW Return computed from these dollars).
        sectors_history_df = pd.DataFrame({
            "Date": [_WINDOW_OPEN, _WINDOW_OPEN,
                     _WINDOW_CLOSE, _WINDOW_CLOSE],
            "Sector": ["Tech", "Health", "Tech", "Health"],
            "TotalValue": [2400.0, 400.0, 3000.0, 500.0],
            "TotalCostBasis": [3000.0, 400.0, 3000.0, 400.0],
        })

        last_filter_call = None

        def get_filtered_dimension_history(self, dimension, account_type=None,
                                           symbols=None, start_date=None):
            # Record the call so routing tests can assert it was used.
            self.last_filter_call = {
                "dimension": dimension, "account_type": account_type,
                "symbols": symbols, "start_date": start_date}
            return pd.DataFrame({
                "Date": [_WINDOW_OPEN, _WINDOW_CLOSE],
                "Sector": ["Tech", "Tech"],
                "total_value": [2000.0, 2500.0],
                "total_cost_basis": [2000.0, 2000.0],
            })

        portfolio_history_df = pd.DataFrame(
            {"Value": [3000.0, 3200.0, 3500.0],
             "CostBasis": [3400.0, 3400.0, 3400.0]},
            index=pd.to_datetime([_WINDOW_OPEN, _WINDOW_MID, _WINDOW_CLOSE]),
        )

        portfolio_assets_history_expanded_df = pd.DataFrame({
            "Date": [_WINDOW_OPEN, _WINDOW_CLOSE,
                     _WINDOW_OPEN, _WINDOW_CLOSE],
            "Symbol": ["AAA", "AAA", "BBB", "BBB"],
            "ClosingPrice": [8.0, 10.0, 25.0, 20.0],
            "Value": [800.0, 1000.0, 2500.0, 2000.0],
        })

    return FakeHandler()


class ScriptedProvider:
    """Returns pre-scripted LLMResponses in order, ignoring inputs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, system, messages, tools):
        self.calls.append({"system": system, "messages": messages})
        return self._responses.pop(0)
