import datetime
import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from libraries.HistoryHandlers import BaseHistoryHandler, AssetHistoryHandler
from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_assets_hypothetical_history_table_sql,
                           read_assets_hypothetical_history_query,
                           read_assets_hypothetical_history_columns)
from libraries.pandas_helpers import print_full, mysql_to_df
from libraries.helpers import (build_master_log, gen_hist_quantities_mult,
                               get_historical_prices,
                               aggregate_assets_history_by_symbol)
from libraries.globals import SYMBOL_BLACKLIST
from pandas.tseries.offsets import BDay

class AssetHypotheticalHistoryHandler(BaseHistoryHandler):
    create_history_table_sql = create_assets_hypothetical_history_table_sql
    history_table_name = 'assets_hypothetical_history'
    
    def __init__(self, symbols: list=[],
                 assets_history_df: pd.DataFrame=None,
                 read_only: bool=None) -> None:
        """ 
        Initialize object with updated asset hypothetical history from DB, 
        which contains the hypotheitcal quantity and value of each asset 
        for each day of existence AFTER it was completely exited 
        
        (Date, Symbol, Quantity, ClosingPrice, Value)
        """
        assert(isinstance(symbols, list))

        # Resolve read_only up front (same rule BaseHistoryHandler.__init__
        # uses) so the nested AssetHistoryHandler construction below -- which
        # runs before super().__init__() -- can forward our actual mode
        # instead of independently falling back to the global.
        self.read_only = self._resolve_read_only(read_only)

        # If symbols is not provided, then populate with
        # all assets which have been completely exited
        # (i.e. quantity = 0)
        
        # Retrieve quantity histories of all assets  
        assets_event_log_df = build_master_log(symbols)
        asset_hist_quantities = gen_hist_quantities_mult(
            assets_event_log_df, 
            cadence='daily',
            expand_chronology=False
        )
        asset_hist_quantities = asset_hist_quantities.sort_index()

        # Filter to just exited assets
        exited_df = asset_hist_quantities[asset_hist_quantities['Quantity'] == 0]
        exited_df = exited_df.reset_index()
        
        # Store symbols and exit dates
        self.symbols = exited_df['Symbol'].unique().tolist()
        self.exit_dates_df = exited_df[['Date', 'Symbol']]

        if assets_history_df is None:
            # Initialize AssetHistoryHandler to ensure that base data is up-to-date
            asset_history_handler = AssetHistoryHandler(
                self.symbols, read_only=self.read_only)
            # Stored history is per-account; this handler works per-symbol.
            self.assets_history_df = aggregate_assets_history_by_symbol(
                asset_history_handler.history_df)
        else:
            self.assets_history_df = assets_history_df

        super().__init__(read_only=read_only)

        actuals_df = self.assets_history_df
        actuals_df['Owned'] = "Actual"
        
        self.history_df = pd.concat([actuals_df, self.history_df])    
        self.history_df = self.history_df.sort_values(by=['Symbol','Date'], ascending=True)

    def set_history(self, start_date: str=None, overwrite: bool=False) -> None:
        """
        For all symbols in self.symbols, update DB with hypothetical
        asset history info up to today
        
        Because each asset has its own exit date, this method will will ignore
        "start_date", and will instead use the data in self.exit_dates for the asset 
        as the date to start from (or latest date in asset_hypothetical_history 
        table for that particular asset)
        
        If overwrite is True, overwrite all existing history in DB with new derived history
        Else, only add new history to DB (append-only)
        
        Args:
             IGNORED - start_date (str): Date to start history from (inclusive)
        """
        # Get history from DB into dataframe
        hypo_df = self.get_history()
        
        # Dictionary to hold {symbol:start_date}, where start_date is the
        # first date for which we should pull historical prices for each symbol
        # (Either the first date after exiting ['create'] or just the date after the latest 
        # date in the table['update'])
        start_dates_dict = {}
        
        # If asset_hypothetical_history table is empty, then use 
        # exit dates (ie date of sale) for all assets as start dates
        if hypo_df.empty:
            for _, row in self.exit_dates_df.iterrows():
                start_date = row['Date'].date()
                symbol = row['Symbol']
                start_dates_dict[symbol] = start_date
        else:
            # Get latest date for each symbol in hypothetical history table
            # IE ideally should be yesterday, 2 days ago, etc
            hypo_history_df = hypo_df.groupby('Symbol')['Date'].max()
            hypo_history_df = hypo_history_df.reset_index()
            
            # Establish most recent day with possible trading (close) data
            today = datetime.datetime.today()
            previous_trading_date = today - BDay(1)
            previous_trading_date = previous_trading_date.date()
             
            for _, row in hypo_history_df.iterrows():
                #Most recent date in database for this symbol
                latest_history_date = row['Date']
                
                # If latest history date is caught up to trading data, then
                # this symbol does not need to be updated
                if latest_history_date >= previous_trading_date:
                    continue
                
                # Set start date for historical prices to the first 
                # trading day after the most recent date
                start_date = latest_history_date + BDay(1)
                start_date = start_date.date()
                symbol = row['Symbol']
                start_dates_dict[symbol] = start_date
                
            # For symbols which are in exit_dates_df but not in hypo_history_df,
            # add them to latest_dates_dict with date from exit dates 
            # (These are newly exited positions, which havent been added to DB yet)
            for _, row in self.exit_dates_df.iterrows():
                start_date = row['Date'].date()
                symbol = row['Symbol']
                if symbol not in hypo_history_df['Symbol'].unique():
                    start_dates_dict[symbol] = start_date
        
        # Remove symbols which are no longer listed on stock exchanges 
        for k in SYMBOL_BLACKLIST: 
            del start_dates_dict[k]
    
        # If there's nothing to update, return
        if len(start_dates_dict) == 0:
            return

        # TODO: Figure out impact and how to handle duplicate exits
        # IE Things which I sold, then rebought, then sold again - HD, DE
        
        # Get earliest date globally, then pull all prices for all symbols from that date
        # Because the call is expensive, it's better to pull all prices at once, from the 
        # global first date, then selectively filter out the rows we don't need from the 
        # dateframe instead of making individual calls for each symbol 
        first_date = min(start_dates_dict.values())
        
        # Only retrieve prices for symbols which are active and 
        # actually in need of updating
        symbols = list(start_dates_dict.keys())
        # Retrieve historical prices for all symbols in need
        prices_df = get_historical_prices(symbols,
                                          start=first_date,
                                          interval='daily',
                                          cleaned_up=True)
        
        # For each symbol, and its start date, filter prices_df to just that symbol 
        # and start date, then append to hist_prices_df
        hist_prices_df = pd.DataFrame()
        for symbol, start_date in start_dates_dict.items():
            start_date = pd.to_datetime(start_date) 
            symbol_prices_df = prices_df.loc[(prices_df['Symbol'] == symbol) & 
                                             (prices_df['Date'] >= start_date)]
            
            hist_prices_df = pd.concat([hist_prices_df, symbol_prices_df])
        
        master_df = pd.DataFrame()
        asset_actuals_df = self.assets_history_df
            
        # Build hypothetical DF, for each symbol 
        for symbol in symbols: 
            # Get actuals history for symbol (to retrieve final quantity)
            asset_actual_df = \
                asset_actuals_df[asset_actuals_df['Symbol'] == symbol]
            asset_actual_df['Owned'] = "Actual"
            
            # Get hypothetical price history for symbol, from exit day to now
            asset_hypo_df = hist_prices_df[hist_prices_df['Symbol'] == symbol]
            asset_hypo_df['Owned'] = "Hypothetical"

            # Stack hypotheticals under actuals, and project last "quantity"
            # forward into every day of hypotheticals
            asset_combined_df = pd.concat([asset_actual_df, asset_hypo_df])
            asset_combined_df[['Quantity', 'ClosingPrice']] = \
                asset_combined_df[['Quantity', 'ClosingPrice']].ffill()  
            asset_combined_df['Date'] = pd.to_datetime(asset_combined_df['Date'])
            
            asset_combined_df['ClosingPrice'] = \
                asset_combined_df['ClosingPrice'].astype(float)
            
            # Generate hypothetical value
            asset_combined_df['Value'] = asset_combined_df['Quantity'] * \
                asset_combined_df['ClosingPrice']

            master_df = pd.concat([master_df, asset_combined_df])
        
        # Filter only hypothetical rows to store into DB
        master_df = master_df[master_df['Owned'] == 'Hypothetical']
        master_df = master_df.dropna(subset=['Quantity', 'ClosingPrice', 'Value'])

        # OPTIMIZATION: Batch insert using executemany() instead of individual INSERTs
        with MysqlDB(dbcfg) as db:
            if overwrite:
                sql = """REPLACE INTO assets_hypothetical_history
                         (date, symbol, quantity, closing_price, value)
                         VALUES (%s, %s, %s, %s, %s)"""
            else:
                sql = """INSERT IGNORE INTO assets_hypothetical_history
                         (date, symbol, quantity, closing_price, value)
                         VALUES (%s, %s, %s, %s, %s)"""

            values = [
                (row['Date'], row['Symbol'], int(row['Quantity']),
                 float(row['ClosingPrice']), float(row['Value']))
                for _, row in master_df.iterrows()
            ]

            if values:
                db.cursor.executemany(sql, values)
                print(f"✓ Batch inserted {len(values)} hypothetical history rows")

    def get_history(self) -> pd.DataFrame:
        """
        For all symbols in self.symbols, get asset hypothetical history from DB into dataframe
        
        REMOVED "with_actuals" FOR NOW, NO USE FOR IT AND IT FORCES A SEPARATE CALL TO DB
        HERE WHICH SLOWS THINGS DOWN (instead of using the history_df attribute which is stored
        in the initialization of this object)
        If with_actuals is True, then also get actuals history from DB into dataframe, and 
        return combined actuals + hypotheticals (with an "Owned" column indicate which is which)
        
        Returns:
            history_df (pd.DataFrame): 
                Date, Symbol, Quantity, ClosingPrice, Value 
        """
        symbols_clause = \
            "(" + ", ".join([f"'{symbol}'" for symbol in self.symbols]) + ")"
        symbols_str = " WHERE symbol IN " + symbols_clause if len(self.symbols) > 0 else ""
    
        query = read_assets_hypothetical_history_query + symbols_str
        history_df = mysql_to_df(query, read_assets_hypothetical_history_columns, dbcfg, 
                                 cached=True)
        
        history_df['Owned'] = "Hypothetical"

        return history_df