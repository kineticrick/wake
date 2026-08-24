#!/usr/bin/env python
"""
Run tracking for generators/daily_update.py.

The read-only web tier never computes freshness itself — it reads the most
recent successful run recorded here. compute_staleness() is deliberately pure
so it can be tested without a database.
"""
import datetime
import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import pandas as pd
from pandas.tseries.offsets import BDay

from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_history_meta_table_sql,
                              insert_history_meta_run_sql,
                              finish_history_meta_run_sql,
                              fail_history_meta_run_sql,
                              read_last_successful_run_query,
                              read_current_prices_freshness_query)
from libraries.globals import PRICE_SNAPSHOT_STALE_HOURS

HISTORY_TABLES = [
    'assets_history',
    'portfolio_history',
    'assets_hypothetical_history',
    'sectors_history',
    'asset_types_history',
    'account_types_history',
    'geography_history',
]


def compute_staleness(as_of, today=None) -> bool:
    """
    True when `as_of` is behind the most recent completed trading day.

    as_of: datetime.date | None -- None (no successful run yet) is always stale.
    today: datetime.date        -- injectable so tests never depend on the clock.
    """
    if as_of is None:
        return True
    if today is None:
        today = datetime.date.today()
    previous_business_date = (pd.Timestamp(today) - BDay(1)).date()
    return as_of < previous_business_date


def gen_history_meta_table() -> None:
    """Create the history_meta table if it does not exist. Write mode only."""
    with MysqlDB(dbcfg) as db:
        db.execute(create_history_meta_table_sql)


def start_run(today=None) -> int:
    """Record the start of an update run. Returns its row id."""
    started = datetime.datetime.now() if today is None else \
        datetime.datetime.combine(today, datetime.time())
    with MysqlDB(dbcfg) as db:
        db.execute(insert_history_meta_run_sql, (started,))
        return db.cursor.lastrowid


def finish_run(run_id: int, tables: dict) -> None:
    """Mark a run successful and record each table's max date."""
    with MysqlDB(dbcfg) as db:
        db.execute(finish_history_meta_run_sql,
                   (datetime.datetime.now(), json.dumps(tables), run_id))


def fail_run(run_id: int, error: str) -> None:
    """Mark a run failed. The DB keeps its last good data."""
    with MysqlDB(dbcfg) as db:
        db.execute(fail_history_meta_run_sql,
                   (datetime.datetime.now(), str(error)[:4000], run_id))


def table_max_dates() -> dict:
    """{table_name: 'YYYY-MM-DD' or None} for every history table."""
    out = {}
    with MysqlDB(dbcfg) as db:
        for table in HISTORY_TABLES:
            db.execute(f"SELECT MAX(date) FROM {table}")
            value = db.fetchone()[0]
            out[table] = str(value) if value is not None else None
    return out


def last_successful_run():
    """
    (as_of, tables) for the most recent successful run.

    as_of is the OLDEST per-table max date, so a partially-updated DB reports
    the date every dimension can actually be trusted to.
    Returns (None, {}) when no successful run has been recorded.
    """
    with MysqlDB(dbcfg) as db:
        db.execute(read_last_successful_run_query)
        row = db.fetchone()

    if not row:
        return None, {}

    _run_finished, tables_json = row
    if isinstance(tables_json, (bytes, bytearray)):
        tables_json = tables_json.decode()
    tables = json.loads(tables_json) if tables_json else {}

    dates = [datetime.date.fromisoformat(v) for v in tables.values() if v]
    return (min(dates) if dates else None), tables


def latest_price_snapshot():
    """
    Newest current_prices.fetched_at, or None if the snapshot table is empty
    (price_snapshot.py has never run) or doesn't exist yet.
    """
    with MysqlDB(dbcfg) as db:
        db.execute(read_current_prices_freshness_query)
        row = db.fetchone()
    return row[0] if row else None


def compute_price_staleness(fetched_at, now=None) -> bool:
    """
    True when the newest price snapshot is older than
    globals.PRICE_SNAPSHOT_STALE_HOURS (see that constant for why a plain
    hour count is the right check here, unlike compute_staleness's
    business-day logic).

    fetched_at: datetime.datetime | None -- None (no snapshot yet) is always stale.
    now:        datetime.datetime        -- injectable so tests never depend on the clock.
    """
    if fetched_at is None:
        return True
    if now is None:
        now = datetime.datetime.now()
    age = now - fetched_at
    return age > datetime.timedelta(hours=PRICE_SNAPSHOT_STALE_HOURS)
