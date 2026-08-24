#!/usr/bin/env python
"""
DemoDashboardHandler — synthetic data layer for demo/showcase mode.

No database or yfinance calls are made. All prices are simulated via
Geometric Brownian Motion with a fixed seed so every run produces identical data.

Activated by setting the PORTFOLIO_DEMO_MODE=1 env var before importing globals.py,
which happens automatically when portfolio_dashboard.py is started with --demo.
"""

import datetime
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import numpy as np
import pandas as pd

import libraries.helpers as helpers
from libraries.helpers import add_asset_info
from visualization.dash.DashboardHandler import DashboardHandler

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
BASE_SEED = 42

# ---------------------------------------------------------------------------
# Asset definitions
# ---------------------------------------------------------------------------
DEMO_ASSETS = [
    {
        'Symbol': 'AAPL', 'Name': 'Apple Inc.',
        'Sector': 'Technology - Software', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 130.0, 'Qty': 150, 'Sigma': 0.020, 'Mu': 0.0005,
        'PurchaseOffset': 0, 'DividendYield': 0.6,
    },
    {
        'Symbol': 'MSFT', 'Name': 'Microsoft Corp.',
        'Sector': 'Technology - Software', 'AssetType': 'Common Stock',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 230.0, 'Qty': 100, 'Sigma': 0.020, 'Mu': 0.0004,
        'PurchaseOffset': 30, 'DividendYield': 0.7,
    },
    {
        'Symbol': 'GOOGL', 'Name': 'Alphabet Inc.',
        'Sector': 'Technology - Software', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 1700.0, 'Qty': 12, 'Sigma': 0.021, 'Mu': 0.0003,
        'PurchaseOffset': 60, 'DividendYield': 0.0,
    },
    {
        'Symbol': 'NVDA', 'Name': 'NVIDIA Corp.',
        'Sector': 'Semiconductors', 'AssetType': 'Common Stock',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 140.0, 'Qty': 80, 'Sigma': 0.025, 'Mu': 0.0008,
        'PurchaseOffset': 10, 'DividendYield': 0.1,
    },
    {
        'Symbol': 'JPM', 'Name': 'JPMorgan Chase',
        'Sector': 'Banking + Finance', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 130.0, 'Qty': 200, 'Sigma': 0.018, 'Mu': 0.0002,
        'PurchaseOffset': 20, 'DividendYield': 2.5,
    },
    {
        'Symbol': 'JNJ', 'Name': 'Johnson & Johnson',
        'Sector': 'Biotech + Pharmaceuticals', 'AssetType': 'Common Stock',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 155.0, 'Qty': 120, 'Sigma': 0.013, 'Mu': -0.0001,
        'PurchaseOffset': 45, 'DividendYield': 3.0,
    },
    {
        'Symbol': 'AMZN', 'Name': 'Amazon.com Inc.',
        'Sector': 'Consumer Discretionary', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 3100.0, 'Qty': 8, 'Sigma': 0.022, 'Mu': 0.0003,
        'PurchaseOffset': 15, 'DividendYield': 0.0,
    },
    {
        'Symbol': 'SPY', 'Name': 'SPDR S&P 500 ETF',
        'Sector': 'Broad Market', 'AssetType': 'ETF',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 370.0, 'Qty': 300, 'Sigma': 0.012, 'Mu': 0.0003,
        'PurchaseOffset': 5, 'DividendYield': 1.3,
    },
    {
        'Symbol': 'VXUS', 'Name': 'Vanguard Total Intl ETF',
        'Sector': 'Broad Market - Intl', 'AssetType': 'ETF',
        'AccountType': 'Retirement', 'Geography': 'ex-US',
        'StartPrice': 55.0, 'Qty': 400, 'Sigma': 0.012, 'Mu': 0.0001,
        'PurchaseOffset': 50, 'DividendYield': 2.8,
    },
    {
        'Symbol': 'VEA', 'Name': 'Vanguard FTSE Dev Markets',
        'Sector': 'Broad Market - Intl', 'AssetType': 'ETF',
        'AccountType': 'Retirement', 'Geography': 'ex-US',
        'StartPrice': 43.0, 'Qty': 250, 'Sigma': 0.012, 'Mu': 0.0001,
        'PurchaseOffset': 50, 'DividendYield': 2.5,
    },
    {
        'Symbol': 'O', 'Name': 'Realty Income Corp.',
        'Sector': 'Real Estate', 'AssetType': 'REIT',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 60.0, 'Qty': 250, 'Sigma': 0.016, 'Mu': -0.0002,
        'PurchaseOffset': 100, 'DividendYield': 5.5,
    },
    {
        'Symbol': 'AMT', 'Name': 'American Tower Corp.',
        'Sector': 'Real Estate', 'AssetType': 'REIT',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 210.0, 'Qty': 60, 'Sigma': 0.016, 'Mu': 0.0002,
        'PurchaseOffset': 80, 'DividendYield': 2.9,
    },
]

# Exited positions — shown in the Hypotheticals tab
DEMO_EXITED_ASSETS = [
    {
        'Symbol': 'META', 'Name': 'Meta Platforms',
        'Sector': 'Technology - Software', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 200.0, 'Qty': 50, 'Sigma': 0.025, 'Mu': 0.0002,
        'PurchaseOffset': 10,
        'ExitDaysAgo': 650,   # ~2.5 years ago in business days
    },
    {
        'Symbol': 'TSLA', 'Name': 'Tesla Inc.',
        'Sector': 'Consumer Discretionary', 'AssetType': 'Common Stock',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 700.0, 'Qty': 20, 'Sigma': 0.035, 'Mu': 0.0003,
        'PurchaseOffset': 20,
        'ExitDaysAgo': 780,   # ~3 years ago in business days
    },
    {
        'Symbol': 'DIS', 'Name': 'Walt Disney Co.',
        'Sector': 'Media + Entertainment', 'AssetType': 'Common Stock',
        'AccountType': 'Discretionary', 'Geography': 'US',
        'StartPrice': 150.0, 'Qty': 80, 'Sigma': 0.018, 'Mu': -0.0001,
        'PurchaseOffset': 30,
        'ExitDaysAgo': 390,   # ~1.5 years ago in business days
    },
    {
        'Symbol': 'PYPL', 'Name': 'PayPal Holdings',
        'Sector': 'Banking + Finance', 'AssetType': 'Common Stock',
        'AccountType': 'Retirement', 'Geography': 'US',
        'StartPrice': 180.0, 'Qty': 60, 'Sigma': 0.022, 'Mu': -0.0003,
        'PurchaseOffset': 25,
        'ExitDaysAgo': 520,   # ~2 years ago in business days
    },
]


def _gbm_prices(start_price: float, mu: float, sigma: float,
                n_days: int, seed: int) -> np.ndarray:
    """Return an array of n_days closing prices via Geometric Brownian Motion.

    price[t] = price[t-1] * exp((mu - 0.5*sigma²)*dt + sigma*sqrt(dt)*Z)
    where dt=1, Z ~ N(0,1), and price[0] = start_price.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0
    z = rng.standard_normal(n_days - 1)
    log_returns = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * z
    prices = np.empty(n_days)
    prices[0] = start_price
    if n_days > 1:
        prices[1:] = start_price * np.exp(np.cumsum(log_returns))
    return prices


class DemoDashboardHandler(DashboardHandler):
    """Dashboard handler that generates and serves fully synthetic portfolio data.

    Inherits all pure-pandas methods from DashboardHandler unchanged.
    Overrides __init__ to skip DB/yfinance and generate synthetic data,
    and overrides _load_hypotheticals / _load_dimension for lazy-loaded tabs.
    """

    def __init__(self) -> None:
        # Standard milestone definitions (identical to parent)
        self.performance_milestones = [
            ('1d', 1), ('1w', 7), ('1m', 30), ('3m', 90),
            ('6m', 180), ('1y', 365), ('2y', 730), ('3y', 1095), ('5y', 1825),
        ]

        print("Generating synthetic demo data...")

        today = pd.Timestamp.today().normalize()
        all_dates = pd.bdate_range('2020-01-02', today)
        n_days = len(all_dates)
        self._all_dates = all_dates
        self._n_days = n_days

        # ----------------------------------------------------------------
        # Pre-generate one GBM price series per asset (deterministic)
        # ----------------------------------------------------------------
        all_asset_defs = DEMO_ASSETS + DEMO_EXITED_ASSETS
        self._demo_prices: dict[str, np.ndarray] = {}
        for i, asset in enumerate(all_asset_defs):
            self._demo_prices[asset['Symbol']] = _gbm_prices(
                asset['StartPrice'], asset['Mu'], asset['Sigma'],
                n_days, seed=BASE_SEED + i,
            )

        # Compute exit indices for exited assets
        # Using BDay offset so exit dates land on trading days
        self._demo_exit_indices: dict[str, int] = {}
        for asset in DEMO_EXITED_ASSETS:
            sym = asset['Symbol']
            exit_ts = (today - pd.tseries.offsets.BDay(asset['ExitDaysAgo'])).normalize()
            idx = int(all_dates.searchsorted(exit_ts, side='right'))
            self._demo_exit_indices[sym] = min(idx, n_days - 1)

        # ================================================================
        # 1. assets_history_df — all 16 assets
        #    Columns: Date, Symbol, Quantity, CostBasis, ClosingPrice,
        #             Value, PercentReturn
        # ================================================================
        history_rows = []

        for asset in DEMO_ASSETS:
            sym = asset['Symbol']
            prices = self._demo_prices[sym]
            po = asset['PurchaseOffset']
            cost_basis_per_share = float(prices[po])
            qty = asset['Qty']
            for d, p in zip(all_dates[po:], prices[po:]):
                pf = float(p)
                history_rows.append({
                    'Date': d,
                    'Symbol': sym,
                    'Quantity': qty,
                    'CostBasis': round(cost_basis_per_share * qty, 2),
                    'ClosingPrice': round(pf, 4),
                    'Value': round(qty * pf, 2),
                    'PercentReturn': round(
                        (pf - cost_basis_per_share) / cost_basis_per_share * 100, 2),
                })

        for asset in DEMO_EXITED_ASSETS:
            sym = asset['Symbol']
            prices = self._demo_prices[sym]
            po = asset['PurchaseOffset']
            exit_idx = self._demo_exit_indices[sym]
            cost_basis_per_share = float(prices[po])
            qty = asset['Qty']
            for d, p in zip(all_dates[po:exit_idx], prices[po:exit_idx]):
                pf = float(p)
                history_rows.append({
                    'Date': d,
                    'Symbol': sym,
                    'Quantity': qty,
                    'CostBasis': round(cost_basis_per_share * qty, 2),
                    'ClosingPrice': round(pf, 4),
                    'Value': round(qty * pf, 2),
                    'PercentReturn': round(
                        (pf - cost_basis_per_share) / cost_basis_per_share * 100, 2),
                })

        self.assets_history_df = pd.DataFrame(history_rows)

        # ================================================================
        # 2. portfolio_assets_history_df — current 12 assets only
        # ================================================================
        current_symbols = [a['Symbol'] for a in DEMO_ASSETS]
        self.portfolio_assets_history_df = self.assets_history_df.loc[
            self.assets_history_df['Symbol'].isin(current_symbols)
        ].copy()

        # ================================================================
        # 3. current_portfolio_summary_df & current_portfolio_value
        #    Columns must match the summary_cols list in _gen_assets_summary
        # ================================================================
        summary_rows = []
        total_value = 0.0

        for asset in DEMO_ASSETS:
            sym = asset['Symbol']
            prices = self._demo_prices[sym]
            po = asset['PurchaseOffset']
            current_price = round(float(prices[-1]), 2)
            cost_basis_per_share = float(prices[po])
            cost_basis = round(cost_basis_per_share * asset['Qty'], 2)
            current_value = round(current_price * asset['Qty'], 2)
            total_value += current_value
            div_yield = asset.get('DividendYield', 0.0)
            total_div = round(current_value * div_yield / 100, 2)
            purchase_date = all_dates[po].date()

            summary_rows.append({
                'Symbol': sym,
                'Name': asset['Name'],
                'Sector': asset['Sector'],
                'AssetType': asset['AssetType'],
                'AccountType': asset['AccountType'],
                'Geography': asset['Geography'],
                'Quantity': asset['Qty'],
                'First Purchase Date': purchase_date,
                'Last Purchase Date': purchase_date,
                'Cost Basis': cost_basis,
                'Current Price': current_price,
                'Current Value': current_value,
                'Dividend Yield': div_yield,
                'Total Dividend': total_div,
            })

        self.current_portfolio_value = round(total_value, 2)
        summary_df = pd.DataFrame(summary_rows)
        summary_df['% Total Portfolio'] = round(
            summary_df['Current Value'] / self.current_portfolio_value * 100, 2)
        summary_df['Lifetime Return'] = round(
            (summary_df['Current Value'] - summary_df['Cost Basis'])
            / summary_df['Cost Basis'] * 100, 2)
        self.current_portfolio_summary_df = summary_df

        # ================================================================
        # 4. portfolio_history_df — datetime-indexed, single Value column
        # ================================================================
        port_hist = (
            self.portfolio_assets_history_df
            .groupby('Date')
            .agg(Value=('Value', 'sum'), CostBasis=('CostBasis', 'sum'))
            .reset_index()
        )
        port_hist['Date'] = pd.to_datetime(port_hist['Date'])
        self.portfolio_history_df = port_hist.set_index('Date')
        # Append today's value + latest cost basis (mirrors DashboardHandler pattern)
        today_cost_basis = float(port_hist['CostBasis'].iloc[-1])
        self.portfolio_history_df.loc[pd.to_datetime('today')] = \
            [self.current_portfolio_value, today_cost_basis]

        # ================================================================
        # 5. Pre-populate helpers._entities_df_cache
        #    All inherited methods that call add_asset_info() will use
        #    this synthetic entities DataFrame — no DB call needed.
        # ================================================================
        entities_df = pd.DataFrame([{
            'Name': a['Name'],
            'Symbol': a['Symbol'],
            'AssetType': a['AssetType'],
            'Sector': a['Sector'],
            'Geography': a['Geography'],
        } for a in all_asset_defs])
        helpers._entities_df_cache = entities_df

        # ================================================================
        # 6. Precompute expanded asset history (% changes + entity info)
        # ================================================================
        print("Precomputing expanded asset history data...")
        self.portfolio_assets_history_expanded_df = self.expand_history_df(
            self.portfolio_assets_history_df.copy())
        print("✓ Expanded asset history precomputed")

        # Per-account expanded history for the per-account Assets chart. Demo
        # symbols are single-account, so attach each symbol's AccountType from
        # the per-account summary, then add entity info.
        acct_map = self.current_portfolio_summary_df[['Symbol', 'AccountType']]
        by_account = self.portfolio_assets_history_df.merge(
            acct_map, on='Symbol', how='left')
        self.portfolio_assets_history_by_account_expanded_df = \
            add_asset_info(by_account)

        # ================================================================
        # 7. Compute milestones and summary using inherited methods
        # ================================================================
        self.asset_milestones = self.get_asset_milestones()
        self.assets_summary_df = self._gen_assets_summary()
        self.portfolio_milestones = self.get_portfolio_milestones()

        # ================================================================
        # 8. Initialise lazy-load cache attributes (same as parent)
        # ================================================================
        self._assets_hypothetical_history_df = None
        self._exits_actuals_history_df = None
        self._exits_hypotheticals_history_df = None
        self._sectors_history_df = None
        self._sectors_summary_df = None
        self._asset_types_history_df = None
        self._asset_types_summary_df = None
        self._account_types_history_df = None
        self._account_types_summary_df = None
        self._geography_history_df = None
        self._geography_summary_df = None

        # Demo data is generated fresh on every run, so it is never stale.
        self.data_as_of = datetime.date.today()
        self._table_dates = {}
        self.is_stale = False

        print("✓ Demo data generation complete")

    def get_freshness(self) -> dict:
        """
        Demo data is generated fresh in memory on every run (no DB, no
        yfinance), so it is never stale -- override rather than inherit the
        parent's DB-backed lookup, which would hit a history_meta /
        current_prices table that demo mode never populates and report
        (incorrectly) stale. Recomputes `data_as_of` to "today" on every call
        so a long-running demo process still reports itself as fresh, matching
        the "generated fresh on every run" contract above.
        """
        return {
            'data_as_of': datetime.date.today(),
            'is_stale': False,
            'price_fetched_at': None,
            'is_price_stale': False,
        }

    # ------------------------------------------------------------------
    # Lazy-load overrides
    # ------------------------------------------------------------------

    def _load_hypotheticals(self):
        """Build synthetic hypothetical history from the 4 exited assets."""
        if self._assets_hypothetical_history_df is not None:
            return

        print("Loading demo hypothetical history data...")
        all_dates = self._all_dates
        rows = []

        for asset in DEMO_EXITED_ASSETS:
            sym = asset['Symbol']
            prices = self._demo_prices[sym]
            po = asset['PurchaseOffset']
            exit_idx = self._demo_exit_indices[sym]
            qty = asset['Qty']

            # Actual period: purchase date → exit date (inclusive of exit date)
            for d, p in zip(all_dates[po:exit_idx], prices[po:exit_idx]):
                rows.append({
                    'Date': d,
                    'Symbol': sym,
                    'Quantity': qty,
                    'ClosingPrice': round(float(p), 4),
                    'Value': round(qty * float(p), 2),
                    'Owned': 'Actual',
                })

            # Hypothetical period: day after exit → today
            for d, p in zip(all_dates[exit_idx:], prices[exit_idx:]):
                rows.append({
                    'Date': d,
                    'Symbol': sym,
                    'Quantity': qty,
                    'ClosingPrice': round(float(p), 4),
                    'Value': round(qty * float(p), 2),
                    'Owned': 'Hypothetical',
                })

        combined = pd.DataFrame(rows)
        combined = combined.sort_values(by=['Symbol', 'Date']).reset_index(drop=True)

        self._assets_hypothetical_history_df = combined
        self._exits_actuals_history_df = \
            combined[combined['Owned'] == 'Actual'].copy()
        self._exits_hypotheticals_history_df = \
            combined[combined['Owned'] == 'Hypothetical'].copy()

        print("✓ Demo hypothetical history loaded")

    def _load_dimension(self, dimension, handler_class):
        """Build synthetic dimension history from precomputed expanded asset history.

        Groups assets by the given dimension and computes the average
        Value % Change per day — same shape as the real handler output.
        """
        dim_map = {
            'Sector': 'sectors',
            'AssetType': 'asset_types',
            'AccountType': 'account_types',
            'Geography': 'geography',
        }
        attr_prefix = dim_map[dimension]
        cache_attr = f'_{attr_prefix}_history_df'
        summary_attr = f'_{attr_prefix}_summary_df'

        if getattr(self, cache_attr) is not None:
            return

        print(f"Loading demo {dimension} history data...")

        expanded = self.portfolio_assets_history_expanded_df.copy()

        # AccountType is not in the entities table, so it's not added by
        # add_asset_info().  Merge it from the portfolio summary instead.
        if dimension == 'AccountType' and 'AccountType' not in expanded.columns:
            acct_map = self.current_portfolio_summary_df[['Symbol', 'AccountType']]
            expanded = expanded.merge(acct_map, on='Symbol', how='left')

        # Sum dollars per (Date, dimension-value) — mirrors the real handlers'
        # total_value/total_cost_basis columns.
        dim_history = (
            expanded
            .groupby(['Date', dimension])
            .agg(TotalValue=('Value', 'sum'),
                 TotalCostBasis=('CostBasis', 'sum'))
            .reset_index()
        )
        # Convert to datetime.date objects (object dtype) to match the real
        # dimension handler output — the tab factory compares Date against a
        # datetime.date and newer pandas rejects datetime64 >= date comparisons.
        dim_history['Date'] = pd.to_datetime(dim_history['Date']).dt.date
        dim_history = dim_history.sort_values(by=[dimension, 'Date'])

        setattr(self, cache_attr, dim_history)
        setattr(self, summary_attr,
                self._gen_summary_df(dimension=dimension, history_df=dim_history))

        print(f"✓ Demo {dimension} history loaded")
