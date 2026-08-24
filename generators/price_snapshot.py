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

from libraries.globals import PORTFOLIO_READ_ONLY
from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_current_prices_table_sql,
                              replace_current_price_sql)
from libraries.helpers import get_portfolio_summary
from libraries.yfinance_helpers import get_current_price


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
    values = [(row['Symbol'], float(row['Current Price']), fetched_at)
              for _, row in prices_df.iterrows()
              if row['Current Price'] is not None]

    with MysqlDB(dbcfg) as db:
        db.cursor.executemany(replace_current_price_sql, values)

    logger.info("Wrote %d prices", len(values))
    return 0


if __name__ == '__main__':
    sys.exit(main())
