#!/usr/bin/env python

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import pandas as pd
from pandas.tseries.offsets import DateOffset
from libraries.pandas_helpers import print_full
from libraries.helpers import (get_portfolio_current_value, add_asset_info,
                               gen_aggregated_historical_value,
                               aggregate_assets_history_by_symbol)

from libraries.HistoryHandlers import AssetHistoryHandler
from libraries.HistoryHandlers import AssetHypotheticalHistoryHandler
from libraries.HistoryHandlers import AssetTypeHistoryHandler
from libraries.HistoryHandlers import PortfolioHistoryHandler
from libraries.HistoryHandlers import SectorHistoryHandler
from libraries.HistoryHandlers import AccountTypeHistoryHandler
from libraries.HistoryHandlers import GeographyHistoryHandler
from libraries.db.history_meta import (compute_staleness, last_successful_run,
                                       latest_price_snapshot,
                                       compute_price_staleness)

class DashboardHandler:
    def __init__(self) -> None:
        # Set milestones
        self.performance_milestones = [
            ('1d', 1), 
            ('1w', 7),
            ('1m', 30),
            ('3m', 90),
            ('6m', 180),
            ('1y', 365),
            ('2y', 730),
            ('3y', 1095),
            ('5y', 1825),
            # Lifetime will also be added when milestones are generated
        ]

        ######## ASSETS ########
        ah = AssetHistoryHandler()
        
        # Get and Set current portfolio value
        # NOTE: Doing this here because it's needed for assets summary, 
        # though it should be in the "PORTFOLIO" section
        portfolio_summary_df, portfolio_value = get_portfolio_current_value()
        self.current_portfolio_summary_df = portfolio_summary_df
        self.current_portfolio_value = portfolio_value
        
        # Get and set assets history. The stored table is per-account; expose
        # the faithful per-account frame (for future per-account asset views)
        # AND a per-symbol-aggregated frame that every current consumer uses.
        self.assets_history_by_account_df = ah.history_df
        self.assets_history_df = aggregate_assets_history_by_symbol(ah.history_df)
        portfolio_symbols = self.current_portfolio_summary_df['Symbol'].tolist()
        self.portfolio_assets_history_df = self.assets_history_df.loc[
            self.assets_history_df['Symbol'].isin(portfolio_symbols)]

        # QUICK WIN: Precompute expanded history data (percentage changes + asset info)
        # This avoids recalculating on every callback
        print("Precomputing expanded asset history data...")
        self.portfolio_assets_history_expanded_df = self.expand_history_df(
            self.portfolio_assets_history_df.copy())
        print("✓ Expanded asset history precomputed")

        # Per-account expanded history for the per-account Assets chart.
        self.portfolio_assets_history_by_account_expanded_df = \
            self._build_by_account_expanded(portfolio_symbols)

        # Get and set asset milestones
        self.asset_milestones = self.get_asset_milestones()

        # Get and set asset summary
        self.assets_summary_df = self._gen_assets_summary()
  
        ####### PORTFOLIO ########
        ph = PortfolioHistoryHandler(assets_history_df = self.assets_history_df)
        
        # Get and Set portfolio history
        self.portfolio_history_df = ph.history_df

        # Index by date - can be done here since portfolio history 
        # has a single set of unique dates (no duplicates)
        self.portfolio_history_df['Date'] = \
            pd.to_datetime(self.portfolio_history_df['Date'])
        self.portfolio_history_df = self.portfolio_history_df.set_index('Date')
        
        # Add current value to portfolio history (carry forward the latest cost
        # basis — portfolio_history_df now has Value AND CostBasis columns, so a
        # scalar assignment would wrongly overwrite both).
        today_cost_basis = float(self.portfolio_history_df['CostBasis'].iloc[-1])
        self.portfolio_history_df.loc[pd.to_datetime('today')] = \
            [self.current_portfolio_value, today_cost_basis]

        # Get and set portfolio milestones
        self.portfolio_milestones = self.get_portfolio_milestones()
  
        ####### LAZY-LOADED HANDLERS #######
        # Hypotheticals and dimension handlers are deferred until first access
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

        ####### DATA FRESHNESS #######
        # Read-only mode never repairs stale data, so the dashboard reports it
        # instead. A missing/failed run reads as stale.
        # These snapshot the freshness at construction time -- DASH_HANDLER is
        # built once at process start (see globals.py), so a value read here
        # would go stale itself the moment the updater fails on some later
        # day. get_freshness() below is re-run on every page load instead;
        # these two attributes are kept for any other introspection that
        # wants the as-of-startup value.
        freshness = self.get_freshness()
        self.data_as_of = freshness['data_as_of']
        self.is_stale = freshness['is_stale']

    def get_freshness(self) -> dict:
        """
        Re-query freshness straight from the DB. Deliberately NOT cached on
        self -- portfolio_dashboard.py calls this on every page load (via a
        callable app.layout) so a server that has been running for days still
        reports an updater failure without needing a restart. Each call is
        one indexed SELECT against history_meta plus one against
        current_prices.

        Returns:
            data_as_of (date|None), is_stale (bool) -- daily_update.py history
            price_fetched_at (datetime|None), is_price_stale (bool) -- price_snapshot.py
        """
        try:
            data_as_of, _table_dates = last_successful_run()
        except Exception:                          # noqa: BLE001 - never block a page load
            data_as_of = None
        is_stale = compute_staleness(data_as_of)

        try:
            price_fetched_at = latest_price_snapshot()
        except Exception:                          # noqa: BLE001 - never block a page load
            price_fetched_at = None
        is_price_stale = compute_price_staleness(price_fetched_at)

        return {
            'data_as_of': data_as_of,
            'is_stale': is_stale,
            'price_fetched_at': price_fetched_at,
            'is_price_stale': is_price_stale,
        }

    def _load_hypotheticals(self):
        """Load hypothetical history data on first access."""
        if self._assets_hypothetical_history_df is None:
            print("Loading hypothetical history data...")
            ahh = AssetHypotheticalHistoryHandler(
                assets_history_df=self.assets_history_df)
            self._assets_hypothetical_history_df = ahh.history_df
            self._exits_actuals_history_df = self._assets_hypothetical_history_df.loc[
                (self._assets_hypothetical_history_df['Owned'] == 'Actual')]
            self._exits_hypotheticals_history_df = self._assets_hypothetical_history_df.loc[
                (self._assets_hypothetical_history_df['Owned'] == 'Hypothetical')]
            print("✓ Hypothetical history loaded")

    @property
    def assets_hypothetical_history_df(self):
        self._load_hypotheticals()
        return self._assets_hypothetical_history_df

    @property
    def exits_actuals_history_df(self):
        self._load_hypotheticals()
        return self._exits_actuals_history_df

    @property
    def exits_hypotheticals_history_df(self):
        self._load_hypotheticals()
        return self._exits_hypotheticals_history_df

    def _load_dimension(self, dimension, handler_class):
        """Load a dimension handler on first access."""
        cache_attr = f'_{dimension.lower()}_history_df'
        summary_attr = f'_{dimension.lower()}_summary_df'
        # Map dimension names to the attribute-style names used in the codebase
        dim_map = {
            'Sector': 'sectors', 'AssetType': 'asset_types',
            'AccountType': 'account_types', 'Geography': 'geography'
        }
        attr_prefix = dim_map[dimension]
        cache_attr = f'_{attr_prefix}_history_df'
        summary_attr = f'_{attr_prefix}_summary_df'

        if getattr(self, cache_attr) is None:
            print(f"Loading {dimension} history data...")
            handler = handler_class()
            setattr(self, cache_attr, handler.history_df)
            setattr(self, summary_attr, self._gen_summary_df(
                dimension=dimension, history_df=handler.history_df))
            print(f"✓ {dimension} history loaded")

    @property
    def sectors_history_df(self):
        self._load_dimension('Sector', SectorHistoryHandler)
        return self._sectors_history_df

    @property
    def sectors_summary_df(self):
        self._load_dimension('Sector', SectorHistoryHandler)
        return self._sectors_summary_df

    @property
    def asset_types_history_df(self):
        self._load_dimension('AssetType', AssetTypeHistoryHandler)
        return self._asset_types_history_df

    @property
    def asset_types_summary_df(self):
        self._load_dimension('AssetType', AssetTypeHistoryHandler)
        return self._asset_types_summary_df

    @property
    def account_types_history_df(self):
        self._load_dimension('AccountType', AccountTypeHistoryHandler)
        return self._account_types_history_df

    @property
    def account_types_summary_df(self):
        self._load_dimension('AccountType', AccountTypeHistoryHandler)
        return self._account_types_summary_df

    @property
    def geography_history_df(self):
        self._load_dimension('Geography', GeographyHistoryHandler)
        return self._geography_history_df

    @property
    def geography_summary_df(self):
        self._load_dimension('Geography', GeographyHistoryHandler)
        return self._geography_summary_df

    def get_filtered_dimension_history(self, dimension, account_type=None,
                                       symbols=None, start_date=None):
        """Aggregated dimension time series, optionally restricted to one
        account_type and/or a symbol subset. Recomputed on demand (cached in
        gen_aggregated_historical_value). Returns columns:
        Date, <dimension>, total_value, total_cost_basis.
        """
        return gen_aggregated_historical_value(
            dimension, symbols=symbols or [], start_date=start_date,
            account_type=account_type)

    def _gen_performance_milestones(self, history_df: pd.DataFrame, current_value: float,
                                    current_price: float=None,  
                                    milestones: list=[]) -> pd.DataFrame: 
        """ 
        Given a history dataframe, current value, and milestone dates, generate 
        a dataframe containing the values at each milestone date, and percentage return
        
        History_df: Must be indexed by date, and contain a column 'Value'
        Milestones: List of (interval, days) tuples
        
        Returns: milestones_df (pd.DataFrame)
            Date, Symbol, Interval, Value, Percent Return
            2023-06-29, Symbol, 1d, 123.45, 0.5 
            
        """
        
        # If milestones is empty, use default milestones
        milestones = milestones if milestones else self.performance_milestones
        
        # Run milestone calculations
        milestone_values = []

        # Populate each milestone date with the value of the portfolio on that date
        for (interval, days) in milestones:
            offset = DateOffset(days=days)
            milestone_date = pd.to_datetime('today') - offset
            milestone_date = milestone_date.strftime('%Y-%m-%d')

            try:
                row = history_df.loc[milestone_date]
            except KeyError:
                continue

            # If loc returns a DataFrame (multiple or zero rows), handle it
            if isinstance(row, pd.DataFrame):
                if row.empty:
                    continue
                row = row.iloc[0]

            milestone_value = row['Value']
            symbol = row['Symbol'] if 'Symbol' in row else "PORTFOLIO"

            milestone_dict = {
                'Date': milestone_date,
                'Symbol': symbol,
                'Interval': interval,
                'Current Value': current_value,
                'Value': milestone_value,
            }

            if current_price is not None:
                milestone_dict['Current Price'] = current_price

            if 'ClosingPrice' in row:
                milestone_dict['Price'] = row['ClosingPrice']

            milestone_values.append(milestone_dict)

        # Get lifetime return
        earliest_date = history_df.index.min().date().strftime('%Y-%m-%d')
        lifetime_row = history_df.loc[earliest_date]
        if isinstance(lifetime_row, pd.DataFrame):
            if lifetime_row.empty:
                return pd.DataFrame(milestone_values)
            lifetime_row = lifetime_row.iloc[0]

        earliest_value = lifetime_row['Value']
        milestone_dict = {
                'Date': earliest_date,
                'Symbol': symbol,
                'Interval': 'Lifetime',
                'Value': earliest_value,
        }
        if 'ClosingPrice' in lifetime_row:
            milestone_dict['Price'] = lifetime_row['ClosingPrice']
        milestone_values.append(milestone_dict)
        
        milestones_df = pd.DataFrame(milestone_values)
        
        milestones_df['Value'] = milestones_df['Value'].astype(float)
        
        # Generate % improvement from each milestone to current value
        milestones_df['Value % Return'] = round(
            (current_value - milestones_df['Value']) \
                / milestones_df['Value'] * 100, 2)

        if current_price is not None:
            milestones_df['Price'] = milestones_df['Price'].astype(float)
            # Generate % improvement from each milestone to current value
            milestones_df['Price % Return'] = round(
                (current_price - milestones_df['Price']) \
                    / milestones_df['Price'] * 100, 2)
        
        return milestones_df
    
    def get_portfolio_milestones(self) -> pd.DataFrame:
        """
        Get value of portfolio at each milestone
        
        Returns: milestones_df (pd.DataFrame)
            Date, Interval, Value, Percent Return
            2023-06-29, 1d, 123.45, 0.5 
        """
        milestones_df = self._gen_performance_milestones(
            self.portfolio_history_df, self.current_portfolio_value)
        
        return milestones_df
        
    def get_asset_milestones(self, symbols: list=[]) -> pd.DataFrame:
        """
        For symbols given, get value of asset at each milestone

        If symbols are not provided, use all symbols in current portfolio

        OPTIMIZED: Collects results and uses single concat instead of repeated concat in loop
        """

        if not symbols:
            symbols = list(self.current_portfolio_summary_df['Symbol'].unique())

        # OPTIMIZATION: Collect all milestones, then concat once
        all_milestones = []

        for symbol in symbols:
            # For each symbol, get the current value and history
            current_price = self.current_portfolio_summary_df.loc[
                self.current_portfolio_summary_df['Symbol'] == symbol]['Current Price'].values[0]
            current_value = self.current_portfolio_summary_df.loc[
                self.current_portfolio_summary_df['Symbol'] == symbol]['Current Value'].values[0]

            history_df = self.assets_history_df.loc[self.assets_history_df['Symbol'] == symbol]

            if history_df.empty:
                print(f"WARNING: No asset history found (local DB) for {symbol}. Ignoring...")
                continue

            # Index date for specific asset, since it's now a unique set of dates
            pd.set_option('mode.chained_assignment',None)
            history_df['Date'] = pd.to_datetime(history_df['Date'])
            history_df = history_df.set_index('Date')

            # Generate milestones for each asset
            asset_milestones_df = \
                self._gen_performance_milestones(history_df, current_value,
                                                 current_price=current_price)

            all_milestones.append(asset_milestones_df)

        # OPTIMIZATION: Single concat instead of repeated concat in loop
        if all_milestones:
            milestones_df = pd.concat(all_milestones)
        else:
            milestones_df = pd.DataFrame()

        return milestones_df
    
    #TODO: Implement this for all assets over entire history - and add N (ie top 5, 10)
    def get_ranked_assets(self,  interval: str, price_or_value: str='price',
                          ascending: bool=False, count: int=None) -> pd.DataFrame:
        """
        Given time interval, rank assets currently in the portfolio by their return
        """
        all_intervals, _ = zip(*self.performance_milestones)
        
        assert(interval in all_intervals)
        assert(price_or_value in ['price', 'value'])
         
        # Get milestones for the given interval
        ranked_assets_df = self.asset_milestones.loc[
            self.asset_milestones['Interval'] == interval]
        
        rank_column = 'Price % Return' if price_or_value == 'price' \
            else 'Value % Return'
        
        ranked_assets_df = ranked_assets_df.sort_values(
            by=rank_column, ascending=ascending)
        
        if count:
            ranked_assets_df = ranked_assets_df.head(count)
        
        return ranked_assets_df
    
    def _gen_pct_change_cols(self, history_df: pd.DataFrame, 
                       column_names: list) -> pd.DataFrame:
        """
        Given a history dataframe with a single set of unique dates + symbol/sector/asset_type, 
        generate a column for % change
        """
        for column_name in column_names:
            history_df[column_name] = history_df[column_name].astype(float)
            history_df[column_name + ' % Change'] = \
                round((history_df[column_name] - history_df[column_name][0]) / 
                    history_df[column_name][0] * 100, 2)
        
        return history_df
    
    def _add_pct_change(self, history_df: pd.DataFrame, 
                       column_names: list, 
                       id_column: str="Symbol") -> pd.DataFrame:
        """
        Given a history dataframe with multiple symbols/sectors (and overlapping dates), 
        generate a column for % change for each symbol
        
        """
        ids = list(history_df[id_column].unique())

        # Process a mini df of each symbol/sector/asset type and collect
        id_dfs = []
        for id in ids:
            id_df = history_df.loc[history_df[id_column] == id]
            id_df = id_df.sort_values(by='Date') \
                if 'Date' in id_df else id_df.sort_index()
            id_df = id_df.reset_index(drop=True)
            id_df = self._gen_pct_change_cols(id_df, column_names)
            id_dfs.append(id_df)

        return pd.concat(id_dfs) if id_dfs else pd.DataFrame()
    
    def expand_history_df(self, history_df: pd.DataFrame, 
                          id_column: str="Symbol") -> pd.DataFrame:
        """ 
        Given a base history dataframe, containing historical quantities, prices and 
        values for multiple symbols or sectors (each with varying dates), add in the following info: 
            - Percent return for each period from the initial price (Price)
            AND/OR
            - Percent return for each period from the initial value (Value)
            AND/OR
            - Info on the asset (Company or Asset Name, Sector, AssetType) [Assets/Symbols only]
            
        "id_column" can be "Symbol" [ie assets], "Sector" or "AssetType"
            [IE etf, common stock, REIT]
            
        Columns added:
            [Assets]
            ClosingPrice % Change, Value % Change
            Name, AssetType, Sector
            
            [Sectors] or [AssetTypes]
            Value % Change
        """
        assert(id_column in ['Symbol', 'Sector', 'AssetType'])
        
        metric_column_names = []
        if "ClosingPrice" in history_df:
            metric_column_names.append("ClosingPrice")

        if "Value" in history_df:
            metric_column_names.append("Value")

        history_df= self._add_pct_change(history_df, metric_column_names, id_column)
        if id_column == "Symbol":
            history_df = add_asset_info(history_df)
        
        return history_df

    def _build_by_account_expanded(self, portfolio_symbols):
        """Per-account asset history (current holdings) with entity info attached.
        One row per (Date, Symbol, AccountType); feeds the per-account Assets
        chart. % change is rebased per line in the chart helper, not here.
        """
        by_account = self.assets_history_by_account_df.loc[
            self.assets_history_by_account_df['Symbol'].isin(portfolio_symbols)]
        return add_asset_info(by_account.copy())

    def gen_historical_stats(self, history_df: pd.DataFrame, 
                             hypotheticals: bool=False) -> pd.DataFrame:
        """ 
        For each symbol (and its history) in history_df, generate the 
        following set of statistics
        
        History_df can be either actuals, or actuals + hypotheticals
        
        Default: 
            - Total/Lifetime Return (Return %, purchase -> current)
            - Max return %, purchase -> current
            - Average Return (Daily)
            - Annualized Return
            - Standard Deviation
            - Sharpe Ratio
            - Sortino Ratio
        Hypotheticals
            - Return %, exit -> current
            - Return %, purchase -> current
            - Max return %, exit -> current
            - Max return %, purchase -> current
        """
        
        # If we're dealing with hypotheticals, split into actuals and hypotheticals
        if hypotheticals and "Owned" in history_df:
            actuals_df = history_df.loc[history_df['Owned'] == 'Actual']
            hypos_df = history_df.loc[history_df['Owned'] == 'Hypothetical']
        # Otherwise we're dealing with only actuals (a currently owned asset),
        # so the entire history is actuals
        else:
            actuals_df = history_df

        # Vectorized: compute per-symbol price stats in one pass
        sorted_actuals = actuals_df.sort_values(by=['Symbol', 'Date'])
        actuals_price_stats = sorted_actuals.groupby('Symbol')['ClosingPrice'].agg(
            enter_price='first', latest_price='last', max_price='max'
        )

        # Compute return percentages vectorized
        stats_df = actuals_price_stats.copy()
        stats_df['Actuals Ret.(Enter/Latest)%'] = round(
            (stats_df['latest_price'] - stats_df['enter_price'])
            / stats_df['enter_price'] * 100, 2)
        stats_df['Actuals Ret.(Enter/Max)%'] = round(
            (stats_df['max_price'] - stats_df['enter_price'])
            / stats_df['enter_price'] * 100, 2)

        #TODO: Add other stats, like stdDev, sharpe, etc

        # If we're dealing with hypotheticals, compute hypo stats vectorized
        if hypotheticals:
            sorted_hypos = hypos_df.sort_values(by=['Symbol', 'Date'])
            hypos_price_stats = sorted_hypos.groupby('Symbol')['ClosingPrice'].agg(
                exit_price='first', latest_hypo_price='last', max_hypo_price='max'
            )

            # Join actuals enter_price with hypo stats
            hypo_stats = hypos_price_stats.join(actuals_price_stats[['enter_price']])

            hypo_stats['Hypo Ret.(Enter/Current)%'] = round(
                (hypo_stats['latest_hypo_price'] - hypo_stats['enter_price'])
                / hypo_stats['enter_price'] * 100, 2)
            hypo_stats['Hypo Ret.(Exit/Current)%'] = round(
                (hypo_stats['latest_hypo_price'] - hypo_stats['exit_price'])
                / hypo_stats['exit_price'] * 100, 2)
            hypo_stats['Hypo Ret.(Enter/Max)%'] = round(
                (hypo_stats['max_hypo_price'] - hypo_stats['enter_price'])
                / hypo_stats['enter_price'] * 100, 2)
            hypo_stats['Hypo Ret.(Exit/Max)%'] = round(
                (hypo_stats['max_hypo_price'] - hypo_stats['exit_price'])
                / hypo_stats['exit_price'] * 100, 2)

            # Merge hypo columns into stats_df (only for symbols that have hypo data)
            hypo_cols = ['Hypo Ret.(Enter/Current)%', 'Hypo Ret.(Exit/Current)%',
                         'Hypo Ret.(Enter/Max)%', 'Hypo Ret.(Exit/Max)%']
            stats_df = stats_df.join(hypo_stats[hypo_cols])

            # Drop symbols with no hypothetical data
            stats_df = stats_df.dropna(subset=hypo_cols, how='all')

        # Clean up: drop intermediate columns, reset index to get Symbol as column
        stats_df = stats_df.drop(
            columns=['enter_price', 'latest_price', 'max_price'],
            errors='ignore')
        stats_df = stats_df.reset_index()

        sort_col = 'Hypo Ret.(Exit/Current)%' \
            if hypotheticals else 'Actuals Ret.(Enter/Latest)%'
        stats_df = stats_df.sort_values(by=sort_col, ascending=False)
        stats_df = add_asset_info(stats_df)

        return stats_df
    
    def _gen_assets_summary(self) -> pd.DataFrame:
        """ 
        Builds a master summary of all assets in the portfolio, including all attributes
        listed in summary_cols and return_cols below
        """
        summary_cols = ['Symbol', 'Name', 'Sector','AssetType', 'AccountType', 
                        'Geography', 'Quantity','First Purchase Date', 
                        'Last Purchase Date', 'Cost Basis','Current Price', 
                        'Current Value', '% Total Portfolio' , 'Lifetime Return', 
                        'Dividend Yield', 'Total Dividend', ]        
        returns_cols = ['1d', '1w', '1m', '3m', '6m', '1y', '2y', '3y', '5y']
        
        # Pivot the milestone returns for all assets into a dataframe with a 
        # single row per unique asset, with columns for each interval
        returns_df = self.asset_milestones 
        returns_df = returns_df.pivot(
            index='Symbol', columns='Interval', values='Price % Return')
        # Only select columns that exist (some intervals may lack historical data)
        available_cols = [col for col in returns_cols if col in returns_df.columns]
        returns_df = returns_df[available_cols]
        
        summary_df = self.current_portfolio_summary_df[summary_cols]
        
        assets_summary_df = pd.merge(summary_df, returns_df, on='Symbol')
        
        return assets_summary_df
       
    def _gen_summary_df(self, dimension: str, 
                        history_df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate a summary of the portfolio, based on the dimension indicated, 
        including the following information
            - Account Type Name
            - Cost Basis
            - Current Market Value
            - % of Total Portfolio
            - Lifetime Return (Sum Cost Basis vs Sum Market Value)
            - VW Return (value-weighted return from latest history dollars)
            - Avg Dividends Yield (% - Avg of all assets dividend yield)
            - Total Dividends ($ - Sum of all assets dividends)
            - TODO: Milestone Returns (1d, 1w, 1m, 3m, 6m, 1y, 2y, 3y, 5y)

        Returns:
            summary_df
        """
        
        assert dimension in ['AccountType', 'AssetType', 'Sector', 'Geography']
        
        # Get the current portfolio summary dataframe
        portfolio_summary_df = self.current_portfolio_summary_df
        summary_df = pd.DataFrame()
        
        # Single groupby with mixed aggregations (sum for financials, mean for yield)
        summary_df = portfolio_summary_df.groupby(dimension).agg({
            'Cost Basis': 'sum',
            'Current Value': 'sum',
            'Total Dividend': 'sum',
            'Dividend Yield': 'mean',
        }).reset_index()
        
        # For % of Total Portfolio, divide current value by total portfolio value
        summary_df['% of Total Portfolio'] = \
            summary_df['Current Value'] / self.current_portfolio_value * 100
        
        # For Lifetime Return, get current value - cost basis / cost basis
        summary_df['Lifetime Return'] = \
            (summary_df['Current Value'] - summary_df['Cost Basis']) \
                / summary_df['Cost Basis'] * 100
        
        # Merge the latest history row, deriving the value-weighted return from
        # the stored dollars (history now stores TotalValue/TotalCostBasis).
        latest_history_date = history_df['Date'].max()
        latest_history_df = history_df.loc[
            history_df['Date'] == latest_history_date].copy()
        latest_history_df = latest_history_df.reset_index(drop=True)
        latest_history_df['VW Return'] = (
            (latest_history_df['TotalValue'] - latest_history_df['TotalCostBasis'])
            / latest_history_df['TotalCostBasis'] * 100)
        latest_history_df = latest_history_df.drop(
            columns=['Date', 'TotalValue', 'TotalCostBasis'])
        summary_df = summary_df.merge(latest_history_df, on=dimension, how='left')
        
        summary_df = summary_df.round(2)
        
        return summary_df