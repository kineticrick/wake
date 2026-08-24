#!/usr/bin/env python
"""
Intraday price snapshot.

Writes the current price of every held symbol to the current_prices table so
the read-only web tier can show live-ish portfolio value without any outbound
network access. Runs every 15 minutes during market hours -- the same window as
the yfinance memoize cache it replaces, so perceived freshness is unchanged.

This job must run with PORTFOLIO_READ_ONLY unset (or '0'): get_current_price()
branches on that flag, and if it were set here the job would read the very
table it's supposed to populate -- a silent no-op that freezes prices forever.
The systemd unit for this job must not set PORTFOLIO_READ_ONLY.

Usage:
    python generators/price_snapshot.py
"""
import datetime
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from libraries.globals import PORTFOLIO_READ_ONLY
from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_current_prices_table_sql,
                              replace_current_price_sql)
from libraries.helpers import get_portfolio_summary
from libraries.yfinance_helpers import get_current_price


def _build_snapshot_values(prices_df: pd.DataFrame,
                           fetched_at: datetime.datetime,
                           logger: logging.Logger) -> list:
    """
    Convert a Symbol/Current Price frame into REPLACE INTO value tuples.

    Drops any row with a null price. A missing yfinance quote (delisted or
    halted ticker) comes back as NaN in a float column, NOT None -- and
    `nan is not None` is True, so an `is not None` check silently lets NaN
    through to the DB write, which then fails on the whole batch. Use
    pd.notna() instead, which is NaN-aware.
    """
    valid_mask = prices_df['Current Price'].apply(pd.notna)
    skipped = sorted(set(prices_df.loc[~valid_mask, 'Symbol']))
    if skipped:
        logger.warning("Skipping %d symbol(s) with a null price: %s",
                       len(skipped), skipped)

    valid_df = prices_df.loc[valid_mask]
    return [(row['Symbol'], float(row['Current Price']), fetched_at)
            for _, row in valid_df.iterrows()]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('price_snapshot')

    if PORTFOLIO_READ_ONLY:
        logger.error(
            "PORTFOLIO_READ_ONLY=1 is set; refusing to run. This job must "
            "fetch from yfinance and write the snapshot -- running read-only "
            "would just read back the table it's supposed to populate.")
        return 1

    with MysqlDB(dbcfg) as db:
        db.execute(create_current_prices_table_sql)

    summary_df = get_portfolio_summary()
    symbols = sorted(summary_df['Symbol'].unique().tolist())
    logger.info("Fetching current prices for %d symbols", len(symbols))

    try:
        prices_df = get_current_price(symbols)
    except Exception:                              # noqa: BLE001 - job boundary
        logger.exception("Price fetch failed; keeping previous snapshot")
        return 1

    if prices_df.empty:
        logger.warning("No prices returned; keeping previous snapshot")
        return 1

    fetched_at = datetime.datetime.now()
    values = _build_snapshot_values(prices_df, fetched_at, logger)

    if not values:
        logger.warning("No valid prices to write; keeping previous snapshot")
        return 1

    try:
        with MysqlDB(dbcfg) as db:
            db.cursor.executemany(replace_current_price_sql, values)
    except Exception:                              # noqa: BLE001 - job boundary
        logger.exception("Writing price snapshot failed; keeping previous "
                         "snapshot")
        return 1

    logger.info("Wrote %d prices", len(values))
    return 0


if __name__ == '__main__':
    sys.exit(main())
