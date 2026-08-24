import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_sectors_history_table_sql,
                              read_sectors_history_query,
                              read_sectors_history_columns)
from libraries.HistoryHandlers import BaseHistoryHandler
from libraries.pandas_helpers import print_full, mysql_to_df
from libraries.helpers import gen_aggregated_historical_value

class SectorHistoryHandler(BaseHistoryHandler):
    create_history_table_sql = create_sectors_history_table_sql
    history_table_name = 'sectors_history'
    
    def __init__(self, read_only: bool=None) -> None:
        """
        Initialize object with updated sector histories from DB.
        (Date, sector, value)
        """
        super().__init__(read_only=read_only)

    def set_history(self, start_date: str=None, overwrite: bool=False) -> None:
        """
        Update DB with sector history info from start_date to today
        
        If overwrite is True, overwrite all existing history in DB with new derived history
        Else, only add new history to DB (append-only)
        
        Args:
            start_date (str): Date to start history from (inclusive)
        """
        # Retrieve daily value data for all sectors
        sectors_historical_data_df = \
            gen_aggregated_historical_value(dimension='Sector',
                                            start_date=start_date)
            
        # OPTIMIZATION: Batch insert using executemany() instead of individual INSERTs
        with MysqlDB(dbcfg) as db:
            if overwrite:
                sql = """REPLACE INTO sectors_history
                         (date, sector, total_value, total_cost_basis)
                         VALUES (%s, %s, %s, %s)"""
            else:
                sql = """INSERT IGNORE INTO sectors_history
                         (date, sector, total_value, total_cost_basis)
                         VALUES (%s, %s, %s, %s)"""

            values = [
                (row['Date'], row['Sector'],
                 float(row['total_value']), float(row['total_cost_basis']))
                for _, row in sectors_historical_data_df.iterrows()
            ]

            if values:
                db.cursor.executemany(sql, values)
                print(f"✓ Batch inserted {len(values)} sector history rows")

        return self.get_history()

    def get_history(self) -> pd.DataFrame:
        """
        Get sector history from DB into dataframe
        
        
        Returns:
            history_df (pd.DataFrame): 
                Date, Sector, TotalValue, TotalCostBasis
        """
        history_df = mysql_to_df(read_sectors_history_query, 
                                 read_sectors_history_columns, dbcfg, cached=True)
        return history_df