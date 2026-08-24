import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_assets_history_table_sql, insert_update_assets_history_sql,
                           insert_ignore_assets_history_sql, read_assets_history_query,
                           read_assets_history_columns)
from libraries.HistoryHandlers import BaseHistoryHandler
from libraries.pandas_helpers import print_full, mysql_to_df
from libraries.helpers import gen_assets_historical_value


def build_assets_history_rows(assets_df):
    """Per-account insert tuples for assets_history, one per input row:
    (date, symbol, account_type, quantity, cost_basis, closing_price,
     value, percent_return)."""
    return [
        (row['Date'], row['Symbol'], row['AccountType'], row['Quantity'],
         row['CostBasis'], row['ClosingPrice'], row['Value'],
         row['PercentReturn'])
        for _, row in assets_df.iterrows()
    ]


class AssetHistoryHandler(BaseHistoryHandler):
    create_history_table_sql = create_assets_history_table_sql
    history_table_name = 'assets_history'
    
    def __init__(self, symbols: list=[], read_only: bool=None) -> None:
        """
        For all symbols in symbols, initialize object with updated
        asset histories from DB. (Date, symbol, quantity, price, value)

        If 'symbols' is empty, initialize object with all asset histories from DB.
        """
        assert(isinstance(symbols, list))
        self.symbols = symbols

        super().__init__(read_only=read_only)

    def set_history(self, start_date: str=None, overwrite: bool=False) -> pd.DataFrame:
        """
        For all symbols in self.symbols, update DB with asset history info
        from start_date to today

        If overwrite is True, overwrite all existing history in DB with new derived history
        Else, only add new history to DB (append-only)

        Args:
            start_date (str): Date to start history from (inclusive)

        Returns:
            pd.DataFrame: The complete updated history from database
        """
        # Retrieve daily quantity + value data for all symbols in self.symbols
        assets_historical_data_df = \
            gen_assets_historical_value(self.symbols, cadence='daily',
                                        start_date=start_date,
                                        include_exit_date=False)

        # OPTIMIZATION: Batch insert using executemany() instead of individual INSERTs
        # This provides 10-50x speedup (see generators/OPTIMIZATION_NOTES.md)
        with MysqlDB(dbcfg) as db:
            if overwrite:
                # Use REPLACE INTO for overwrite
                sql = """REPLACE INTO assets_history
                         (date, symbol, account_type, quantity, cost_basis, closing_price, value, percent_return)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
            else:
                # Use INSERT IGNORE for append-only
                sql = """INSERT IGNORE INTO assets_history
                         (date, symbol, account_type, quantity, cost_basis, closing_price, value, percent_return)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""

            # One row per (date, symbol, account_type) — no collapsing, no drops.
            values = build_assets_history_rows(assets_historical_data_df)

            if values:
                db.cursor.executemany(sql, values)
                print(f"✓ Batch inserted {len(values)} asset history rows")

        # OPTIMIZATION: Return the full history instead of requiring a re-read
        return self.get_history()
            
    def get_history(self) -> pd.DataFrame:
        """
        For all symbols in self.symbols, get asset history from DB into dataframe
        
        
        Returns:
            history_df (pd.DataFrame): 
                Date, Symbol, Quantity, ClosingPrice, Value, CostBasis  
        """
        symbols_clause = \
            "(" + ", ".join([f"'{symbol}'" for symbol in self.symbols]) + ")"
        symbols_str = " WHERE symbol IN " + symbols_clause if len(self.symbols) > 0 else ""
        
        query = read_assets_history_query + symbols_str
        history_df = mysql_to_df(query, read_assets_history_columns, dbcfg, cached=True)
        return history_df
    
# ah = AssetHistoryHandler()
# print_full(ah.get_history())