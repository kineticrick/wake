#!/usr/bin/env python
import datetime
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import pandas as pd
from pandas.tseries.offsets import Day, BDay
from libraries.db import dbcfg, MysqlDB
from libraries.db.mysql_helpers import mysql_cache_evict
from libraries.db.sql import history_table_indexes
from libraries.globals import MYSQL_CACHE_HISTORY_TAG, PORTFOLIO_READ_ONLY

class BaseHistoryHandler:
    # Placeholder for SQL to create history table in DB
    create_history_table_sql = None
    # Table name used for index lookup (set by subclasses or derived from SQL)
    history_table_name = None
    
    def __init__(self, read_only: bool = None) -> None:
        """
        Initialize handler with history from DB, for either assets or total portfolio.

        read_only:
            True  -> read the existing history and stop. No DDL, no yfinance,
                     no writes. Used by the Dash web tier.
            False -> current behavior: create the table if needed and bring the
                     history up to date. Used by generators/daily_update.py.
            None  -> take the value from globals.PORTFOLIO_READ_ONLY.
        """
        self.read_only = (
            PORTFOLIO_READ_ONLY if read_only is None else read_only)

        if self.read_only:
            # The single reason this mode exists: in write mode a stale table
            # triggers set_history(), which fetches 139 tickers from yfinance
            # (58-73s) synchronously inside whatever called us.
            self.history_df = self.get_history()
            self.latest_history_date = self.get_latest_date()
            return

        # Initialize {asset,portfolio,asset_hypothetical}_history table in DB, if not already present
        self.gen_table()
        
        # Retrieve history from DB
        self.history_df = self.get_history()
        
        # OPTIMIZATION: Remove double-read by having set_history() return updated data
        if not self.history_df.empty:

            # Get latest date from dataframe
            latest_history_date = self.history_df['Date'].max()

            today = datetime.datetime.today()
            previous_business_date = today  - BDay(1)
            previous_business_date = previous_business_date.date()

            # If latest history date in DB is behind most recent trading day,
            # update history from day after latest date to today

            # OR if yesterday was a weekend day and latest
            # history date is behind that, then also update history
            # to fill in weekend gaps

            yesterday = today - Day(1)
            yesterday = yesterday.date()
            yesterday_weekend = yesterday.weekday() >= 5

            if latest_history_date < previous_business_date or \
                (yesterday_weekend and latest_history_date < yesterday):
                    self.set_history(start_date=latest_history_date + Day(1))
                    # Evict the stale history cache BEFORE re-reading, otherwise
                    # get_history() returns the pre-update snapshot (the rows
                    # set_history() just wrote stay hidden behind the cache).
                    mysql_cache_evict(MYSQL_CACHE_HISTORY_TAG)
                    updated_df = self.get_history()
                    if updated_df is not None and not updated_df.empty:
                        # Merge new data with existing
                        self.history_df = updated_df

        # If dataframe is empty, update history from start of time to today
        else:
            self.set_history()
            # Evict the stale history cache BEFORE re-reading (see note above).
            mysql_cache_evict(MYSQL_CACHE_HISTORY_TAG)
            self.history_df = self.get_history()
            
        self.latest_history_date = self.get_latest_date()
    
    def gen_table(self) -> None:
        """
        Generate history table in DB if not already present, and create indexes.
        """
        with MysqlDB(dbcfg) as db:
            db.execute(self.create_history_table_sql)

            # Create indexes for this table if defined
            if self.history_table_name and self.history_table_name in history_table_indexes:
                for index_sql in history_table_indexes[self.history_table_name]:
                    db.create_index_safe(index_sql)
        
    def get_history(self) -> pd.DataFrame:
        """Retrieve history from database. Implemented by subclasses."""
        pass

    def set_history(self, start_date=None) -> pd.DataFrame:
        """
        Update history in database and return the full updated dataframe.
        This avoids the need to re-read from DB after writing.

        Returns:
            pd.DataFrame: The complete history including newly added data
        """
        pass
    
    def get_latest_date(self):
        """
        Get date of most recent entry available in DB, or None if there is none.

        Returns:
            latest_date (datetime.date | None)
        """
        if self.history_df is None or self.history_df.empty:
            return None
        return self.history_df['Date'].max()