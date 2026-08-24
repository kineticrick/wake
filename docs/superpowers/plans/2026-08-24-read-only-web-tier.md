# Read-Only Web Tier + Scheduled Updater Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 58–73 second stall that happens the first time a dimension tab is opened each day, by moving all price fetching and history computation out of the web request path into scheduled jobs.

**Architecture:** Split into a *write tier* (`generators/daily_update.py` + `generators/price_snapshot.py`, driven by systemd user timers) and a *read-only web tier* (the Dash app, run with `PORTFOLIO_READ_ONLY=1`). The web tier makes zero outbound network calls and zero database writes, so it can run under a read-only MySQL grant. Freshness is recorded in a new `history_meta` table and surfaced as a banner instead of being repaired inline.

**Tech Stack:** Python 3.14, pandas 3.0.5, Dash 4.4.1, dash-mantine-components 2.8.0, MySQL (mysql-connector-python 26.7.0), yfinance 1.6.0, diskcache, unittest (pytest available as a runner only).

**Spec:** `docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md`

## Global Constraints

- Tests use the standard library `unittest`. Run with `python -m unittest ...`. Do not add a pytest dependency to test code.
- Run everything inside the project venv: `source venv/bin/activate` (Python 3.14).
- Do **not** add new third-party dependencies. Everything here uses the existing stack.
- Never pass bare `'M'`/`'Q'`/`'Y'` pandas offset aliases; use `'ME'`/`'QE'`/`'YE'` (see `libraries/globals.py` CADENCE_MAP note). `'W-FRI'` is the weekly alias in use.
- All new date-dependent logic must accept an injectable `today` parameter, and tests must pass explicit dates. The repo has been bitten before by fixtures anchored to fixed dates (commit `6870604`).
- Write-mode behavior must not change. `generators/importer.py`, `generators/rebuild_asset_history.py`, and `generators/summary_table_generator.py` keep working exactly as they do today.
- End every commit message with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Work on branch `perf/read-only-web-tier` (already created; the spec is committed there as `4421d92`).

---

## File Structure

**Created:**
- `libraries/db/history_meta.py` — run-tracking + pure staleness computation
- `libraries/downsample.py` — pure time-series downsampling helper
- `generators/daily_update.py` — nightly derived-data updater entry point
- `generators/price_snapshot.py` — 15-minute intraday price snapshot job
- `deploy/systemd/wake-daily-update.service` / `.timer`
- `deploy/systemd/wake-price-snapshot.service` / `.timer`
- `tests/libraries/test_history_meta.py`
- `tests/libraries/test_downsample.py`
- `tests/libraries/test_read_only_mode.py`
- `tests/generators/__init__.py`, `tests/generators/test_daily_update.py`

**Modified:**
- `libraries/globals.py` — add `PORTFOLIO_READ_ONLY`, `DOWNSAMPLE_DAILY_WINDOW_DAYS`, `AGGREGATION_CACHE_MAX_ENTRIES`
- `libraries/helpers.py:517-537` (vectorize), `:541` (`_aggregation_cache`)
- `libraries/HistoryHandlers/BaseHistoryHandler.py:21-73` — read-only mode
- All seven handler subclasses — thread `read_only` through `__init__`
- `libraries/db/sql.py` — `history_meta` + `current_prices` DDL and queries
- `libraries/yfinance_helpers/yfinancelib.py:207` — `get_current_price` reads DB when read-only
- `visualization/dash/DashboardHandler.py` — `data_as_of` / `is_stale`
- `visualization/dash/DemoDashboardHandler.py` — same attributes, always fresh
- `visualization/dash/portfolio_dashboard/portfolio_dashboard.py` — staleness banner
- `visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py` — downsampling
- `visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py` — downsampling + top-N default
- `README.md`, `CLAUDE.md` — new commands and deployment
- `PERFORMANCE_ANALYSIS.md`, `FRAMEWORK_COMPARISON.md`, `OPTIMIZATION.md`, `QUICK_WINS_IMPLEMENTED.md`, `MAJOR_OPTIMIZATIONS_IMPLEMENTED.md` — superseded notices

---

### Task 1: Vectorize `aggregate_assets_history_by_symbol`

The current implementation uses a row-wise `.apply()` over ~114k rows (0.83s). The vectorized form was verified to produce a byte-identical frame.

**Files:**
- Modify: `libraries/helpers.py:517-537`
- Test: `tests/libraries/test_aggregate_by_symbol.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `aggregate_assets_history_by_symbol(df: pd.DataFrame) -> pd.DataFrame` — unchanged signature and output. Columns: `Date, Symbol, Quantity, CostBasis, ClosingPrice, Value, PercentReturn`.

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_aggregate_by_symbol.py`:

```python
import datetime
import unittest

import pandas as pd

from libraries.helpers import aggregate_assets_history_by_symbol


def _reference_impl(df):
    """The original row-wise implementation, kept as the equivalence oracle."""
    if df.empty:
        return df
    out = df.groupby(['Date', 'Symbol'], as_index=False).agg(
        Quantity=('Quantity', 'sum'),
        CostBasis=('CostBasis', 'sum'),
        ClosingPrice=('ClosingPrice', 'first'),
        Value=('Value', 'sum'),
    )
    out['PercentReturn'] = out.apply(
        lambda r: (r['Value'] - r['CostBasis']) / r['CostBasis'] * 100
        if r['CostBasis'] else 0.0, axis=1)
    cols = ['CostBasis', 'ClosingPrice', 'Value', 'PercentReturn']
    out[cols] = out[cols].round(2)
    return out


class TestAggregateAssetsHistoryBySymbol(unittest.TestCase):

    def setUp(self):
        d1 = datetime.date(2026, 1, 5)
        d2 = datetime.date(2026, 1, 6)
        # QQQ is held in TWO accounts on the same date (the multi-account case),
        # ZERO has a zero cost basis (the division-guard case),
        # EXIT went to quantity 0 (the exited-asset case).
        self.df = pd.DataFrame([
            {'Date': d1, 'Symbol': 'QQQ',  'Quantity': 10.0, 'CostBasis': 1000.0,
             'ClosingPrice': 150.0, 'Value': 1500.0},
            {'Date': d1, 'Symbol': 'QQQ',  'Quantity': 5.0,  'CostBasis': 400.0,
             'ClosingPrice': 150.0, 'Value': 750.0},
            {'Date': d1, 'Symbol': 'ZERO', 'Quantity': 3.0,  'CostBasis': 0.0,
             'ClosingPrice': 20.0,  'Value': 60.0},
            {'Date': d2, 'Symbol': 'EXIT', 'Quantity': 0.0,  'CostBasis': 0.0,
             'ClosingPrice': 12.0,  'Value': 0.0},
            {'Date': d2, 'Symbol': 'QQQ',  'Quantity': 15.0, 'CostBasis': 1400.0,
             'ClosingPrice': 151.0, 'Value': 2265.0},
        ])

    def test_matches_reference_implementation(self):
        expected = _reference_impl(self.df.copy())
        actual = aggregate_assets_history_by_symbol(self.df.copy())
        pd.testing.assert_frame_equal(actual, expected)

    def test_zero_cost_basis_yields_zero_return_not_inf(self):
        out = aggregate_assets_history_by_symbol(self.df.copy())
        zero_row = out[(out['Symbol'] == 'ZERO')].iloc[0]
        self.assertEqual(zero_row['PercentReturn'], 0.0)

    def test_multi_account_rows_are_summed(self):
        out = aggregate_assets_history_by_symbol(self.df.copy())
        d1 = datetime.date(2026, 1, 5)
        qqq = out[(out['Symbol'] == 'QQQ') & (out['Date'] == d1)].iloc[0]
        self.assertEqual(qqq['Quantity'], 15.0)
        self.assertEqual(qqq['CostBasis'], 1400.0)
        self.assertEqual(qqq['Value'], 2250.0)

    def test_empty_frame_passes_through(self):
        empty = pd.DataFrame(
            columns=['Date', 'Symbol', 'Quantity', 'CostBasis',
                     'ClosingPrice', 'Value'])
        out = aggregate_assets_history_by_symbol(empty)
        self.assertTrue(out.empty)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify the current implementation passes**

Run: `python -m unittest tests.libraries.test_aggregate_by_symbol -v`
Expected: PASS. This is a characterization test — it locks in current behavior *before* the change, so the refactor has an oracle.

- [ ] **Step 3: Replace the row-wise apply with the vectorized form**

In `libraries/helpers.py`, replace lines 534-536 (the `out['PercentReturn'] = out.apply(...)` block) with:

```python
    # Vectorized: .where(cb != 0) makes zero-cost rows NaN, which fillna(0.0)
    # then turns into 0.0 — matching the old `if r['CostBasis'] else 0.0` guard
    # without the 457k-call row-wise apply (0.83s -> 0.03s on ~114k rows).
    cost_basis = out['CostBasis']
    out['PercentReturn'] = (
        (out['Value'] - cost_basis) / cost_basis.where(cost_basis != 0) * 100
    ).fillna(0.0)
```

- [ ] **Step 4: Run the test to verify it still passes**

Run: `python -m unittest tests.libraries.test_aggregate_by_symbol -v`
Expected: PASS, all four tests.

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add libraries/helpers.py tests/libraries/test_aggregate_by_symbol.py
git commit -m "perf: vectorize aggregate_assets_history_by_symbol

Replaces a row-wise apply over ~114k rows (0.83s) with a vectorized
division guarded by .where(cb != 0). Output verified byte-identical
against the previous implementation.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Read-only mode in `BaseHistoryHandler`

This is the core fix. In read-only mode the handler performs exactly one action — `get_history()` — and never calls `gen_table()` (DDL) or `set_history()` (yfinance + writes).

**Files:**
- Modify: `libraries/globals.py` (append near the `MYSQL_CACHE_*` block)
- Modify: `libraries/HistoryHandlers/BaseHistoryHandler.py:21-73`
- Modify: `libraries/HistoryHandlers/AssetHistoryHandler.py:32-42`
- Modify: `libraries/HistoryHandlers/PortfolioHistoryHandler.py:19-28`
- Modify: `libraries/HistoryHandlers/AssetHypotheticalHistoryHandler.py:24-40`
- Modify: `libraries/HistoryHandlers/SectorHistoryHandler.py:22-27`
- Modify: `libraries/HistoryHandlers/AssetTypeHistoryHandler.py:19-24`
- Modify: `libraries/HistoryHandlers/AccountTypeHistoryHandler.py:19-24`
- Modify: `libraries/HistoryHandlers/GeographyHistoryHandler.py:19-24`
- Test: `tests/libraries/test_read_only_mode.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `libraries.globals.PORTFOLIO_READ_ONLY: bool` — true when env `PORTFOLIO_READ_ONLY` is `'1'`.
  - `BaseHistoryHandler.__init__(self, read_only: bool = None)` — `None` means "use the global".
  - `BaseHistoryHandler.read_only: bool` instance attribute.
  - All seven subclasses accept a trailing `read_only: bool = None` keyword and forward it.

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_read_only_mode.py`:

```python
import datetime
import unittest

import pandas as pd

from libraries.HistoryHandlers.BaseHistoryHandler import BaseHistoryHandler


def _make_handler_class(calls, history_df):
    """A handler that records which lifecycle methods actually ran."""

    class FakeHandler(BaseHistoryHandler):
        def gen_table(self_inner):
            calls.append('gen_table')

        def get_history(self_inner):
            calls.append('get_history')
            return history_df.copy()

        def set_history(self_inner, start_date=None, overwrite=False):
            calls.append('set_history')
            raise AssertionError(
                "set_history() must never run in read-only mode — it is the "
                "code path that calls yfinance from inside a Dash callback.")

    return FakeHandler


class TestReadOnlyMode(unittest.TestCase):

    def setUp(self):
        # Deliberately stale: 40 days behind, so write mode WOULD try to update.
        self.stale_date = datetime.date.today() - datetime.timedelta(days=40)
        self.stale_df = pd.DataFrame({'Date': [self.stale_date]})

    def test_read_only_skips_ddl_and_writes_even_when_stale(self):
        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        handler = FakeHandler(read_only=True)

        self.assertEqual(calls, ['get_history'])
        self.assertNotIn('set_history', calls)
        self.assertNotIn('gen_table', calls)

    def test_read_only_still_exposes_latest_history_date(self):
        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        handler = FakeHandler(read_only=True)

        self.assertEqual(handler.latest_history_date, self.stale_date)
        self.assertTrue(handler.read_only)

    def test_read_only_tolerates_an_empty_table(self):
        calls = []
        empty = pd.DataFrame(columns=['Date'])
        FakeHandler = _make_handler_class(calls, empty)

        handler = FakeHandler(read_only=True)

        self.assertIsNone(handler.latest_history_date)
        self.assertTrue(handler.history_df.empty)

    def test_read_only_defaults_from_global(self):
        import libraries.HistoryHandlers.BaseHistoryHandler as base_mod

        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        original = base_mod.PORTFOLIO_READ_ONLY
        base_mod.PORTFOLIO_READ_ONLY = True
        try:
            FakeHandler()          # no explicit argument
        finally:
            base_mod.PORTFOLIO_READ_ONLY = original

        self.assertEqual(calls, ['get_history'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_read_only_mode -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'read_only'`, and `ImportError`/`AttributeError` for `PORTFOLIO_READ_ONLY`.

- [ ] **Step 3: Add the global flag**

In `libraries/globals.py`, immediately after the `MYSQL_CACHE_TTL` line:

```python
# When true, the process must NEVER write to the database or make outbound
# network calls. The Dash web tier runs this way so it can be served from a
# read-only MySQL grant with no internet egress; all history updates happen in
# generators/daily_update.py instead. See
# docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md
PORTFOLIO_READ_ONLY = os.environ.get('PORTFOLIO_READ_ONLY') == '1'
```

- [ ] **Step 4: Add read-only mode to `BaseHistoryHandler`**

In `libraries/HistoryHandlers/BaseHistoryHandler.py`, extend the import at the top:

```python
from libraries.globals import MYSQL_CACHE_HISTORY_TAG, PORTFOLIO_READ_ONLY
```

Replace the `def __init__(self) -> None:` signature and insert the early return immediately after the docstring, before the existing `self.gen_table()` call:

```python
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
```

Then replace `get_latest_date` so an empty table returns `None` instead of `NaN`:

```python
    def get_latest_date(self):
        """
        Get date of most recent entry available in DB, or None if there is none.

        Returns:
            latest_date (datetime.date | None)
        """
        if self.history_df is None or self.history_df.empty:
            return None
        return self.history_df['Date'].max()
```

- [ ] **Step 5: Thread `read_only` through all seven subclasses**

Each subclass currently calls `super().__init__()` with no arguments. Add the keyword to each signature and forward it.

`libraries/HistoryHandlers/AssetHistoryHandler.py` — line 32:
```python
    def __init__(self, symbols: list=[], read_only: bool=None) -> None:
```
and line 42:
```python
        super().__init__(read_only=read_only)
```

`libraries/HistoryHandlers/PortfolioHistoryHandler.py` — line 19:
```python
    def __init__(self, assets_history_df: pd.DataFrame=None,
                 read_only: bool=None) -> None:
```
and line 28:
```python
        super().__init__(read_only=read_only)
```

`libraries/HistoryHandlers/AssetHypotheticalHistoryHandler.py` — line 24:
```python
    def __init__(self, symbols: list=[],
                 assets_history_df: pd.DataFrame=None,
                 read_only: bool=None) -> None:
```
and its `super().__init__()` call:
```python
        super().__init__(read_only=read_only)
```

`SectorHistoryHandler.py`, `AssetTypeHistoryHandler.py`, `AccountTypeHistoryHandler.py`, `GeographyHistoryHandler.py` — each has the identical two-line shape. In each file change:
```python
    def __init__(self, read_only: bool=None) -> None:
```
and:
```python
        super().__init__(read_only=read_only)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_read_only_mode -v`
Expected: PASS, all four tests.

- [ ] **Step 7: Verify write mode is unchanged**

Run: `python -m unittest tests.libraries.test_base_history_handler -v`
Expected: PASS — the existing cache-ordering regression test must still pass, proving write mode was not disturbed.

- [ ] **Step 8: Verify the stall is actually gone**

```bash
PORTFOLIO_READ_ONLY=1 python -c "
import time, sys; sys.path.insert(0, '.')
from libraries.HistoryHandlers import SectorHistoryHandler
t = time.perf_counter()
h = SectorHistoryHandler()
print(f'read-only SectorHistoryHandler: {time.perf_counter()-t:.2f}s rows={len(h.history_df)}')
"
```
Expected: well under 1 second, regardless of how stale `sectors_history` is.

- [ ] **Step 9: Commit**

```bash
git add libraries/globals.py libraries/HistoryHandlers/ tests/libraries/test_read_only_mode.py
git commit -m "feat: add read-only mode to history handlers

With PORTFOLIO_READ_ONLY=1 a handler performs only get_history() --
no DDL, no yfinance, no writes. This severs the only code path from a
Dash callback to a 58-73s yfinance fetch, and lets the web tier run
under a read-only MySQL grant.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `history_meta` table and staleness computation

**Files:**
- Modify: `libraries/db/sql.py` (append)
- Create: `libraries/db/history_meta.py`
- Test: `tests/libraries/test_history_meta.py` (create)

**Interfaces:**
- Consumes: `MysqlDB`, `dbcfg` from `libraries.db`; `mysql_to_df` from `libraries.pandas_helpers`.
- Produces:
  - `compute_staleness(as_of: datetime.date | None, today: datetime.date = None) -> bool` — **pure**, no DB.
  - `gen_history_meta_table() -> None`
  - `start_run(today: datetime.date = None) -> int` (returns `run_id`)
  - `finish_run(run_id: int, tables: dict[str, str]) -> None`
  - `fail_run(run_id: int, error: str) -> None`
  - `last_successful_run() -> tuple[datetime.date | None, dict]` (returns `(as_of, tables)`)

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_history_meta.py`:

```python
import datetime
import unittest

from libraries.db.history_meta import compute_staleness


class TestComputeStaleness(unittest.TestCase):
    """
    Staleness is 'is the data behind the most recent completed trading day'.
    Every case passes `today` explicitly so the test never depends on when it runs.
    """

    def test_no_data_at_all_is_stale(self):
        self.assertTrue(
            compute_staleness(None, today=datetime.date(2026, 8, 25)))

    def test_data_from_previous_business_day_is_fresh(self):
        # Tuesday 2026-08-25; previous business day is Monday 2026-08-24.
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 24),
                              today=datetime.date(2026, 8, 25)))

    def test_data_from_today_is_fresh(self):
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 25),
                              today=datetime.date(2026, 8, 25)))

    def test_data_from_last_week_is_stale(self):
        self.assertTrue(
            compute_staleness(datetime.date(2026, 8, 18),
                              today=datetime.date(2026, 8, 25)))

    def test_friday_data_is_fresh_on_monday(self):
        # Monday 2026-08-24; previous business day is Friday 2026-08-21.
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 21),
                              today=datetime.date(2026, 8, 24)))

    def test_friday_data_is_fresh_on_saturday(self):
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 21),
                              today=datetime.date(2026, 8, 22)))

    def test_thursday_data_is_stale_on_monday(self):
        self.assertTrue(
            compute_staleness(datetime.date(2026, 8, 20),
                              today=datetime.date(2026, 8, 24)))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_history_meta -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libraries.db.history_meta'`

- [ ] **Step 3: Add the DDL and queries**

Append to `libraries/db/sql.py`:

```python
# history_meta - records each daily_update.py run so the read-only web tier
# can report freshness without recomputing MAX(date) across seven tables.
create_history_meta_table_sql = \
    ("CREATE TABLE IF NOT EXISTS history_meta ("
     "id INT AUTO_INCREMENT PRIMARY KEY, "
     "run_started DATETIME NOT NULL, "
     "run_finished DATETIME NULL, "
     "status ENUM('running','success','failed') NOT NULL, "
     "tables_json JSON NULL, "
     "error TEXT NULL)")

insert_history_meta_run_sql = \
    ("INSERT INTO history_meta (run_started, status) VALUES (%s, 'running')")

finish_history_meta_run_sql = \
    ("UPDATE history_meta SET run_finished = %s, status = 'success', "
     "tables_json = %s WHERE id = %s")

fail_history_meta_run_sql = \
    ("UPDATE history_meta SET run_finished = %s, status = 'failed', "
     "error = %s WHERE id = %s")

read_last_successful_run_query = \
    ("SELECT run_finished, tables_json FROM history_meta "
     "WHERE status = 'success' ORDER BY run_finished DESC LIMIT 1")
```

- [ ] **Step 4: Write `libraries/db/history_meta.py`**

```python
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
                              read_last_successful_run_query)

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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_history_meta -v`
Expected: PASS, all seven tests.

- [ ] **Step 6: Create the table against the real DB and sanity-check**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from libraries.db.history_meta import gen_history_meta_table, last_successful_run
gen_history_meta_table()
print('as_of, tables:', last_successful_run())
"
```
Expected: prints `as_of, tables: (None, {})` — no runs recorded yet, and no exception.

- [ ] **Step 7: Commit**

```bash
git add libraries/db/sql.py libraries/db/history_meta.py tests/libraries/test_history_meta.py
git commit -m "feat: add history_meta run tracking and staleness computation

Records each updater run so the read-only web tier can report freshness
without recomputing MAX(date) across seven tables. compute_staleness()
is pure and takes an injectable 'today' so tests never depend on the clock.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `generators/daily_update.py`

**Files:**
- Create: `generators/daily_update.py`
- Create: `tests/generators/__init__.py`
- Test: `tests/generators/test_daily_update.py` (create)

**Interfaces:**
- Consumes: `read_only` keyword on all seven handlers (Task 2); `start_run`, `finish_run`, `fail_run`, `table_max_dates`, `gen_history_meta_table` (Task 3); `aggregate_assets_history_by_symbol` (Task 1).
- Produces: `run_update(logger) -> dict` (the per-table max dates) and `main() -> int` (process exit code).

- [ ] **Step 1: Write the failing test**

Create `tests/generators/__init__.py` (empty file), then `tests/generators/test_daily_update.py`:

```python
import logging
import unittest
from unittest import mock

import pandas as pd

from generators import daily_update


class TestDailyUpdateOrchestration(unittest.TestCase):
    """
    daily_update must construct every handler in WRITE mode and in dependency
    order, because Portfolio and Hypothetical history are both derived from
    the freshly-updated asset history.
    """

    def setUp(self):
        self.logger = logging.getLogger('test_daily_update')
        self.logger.addHandler(logging.NullHandler())
        self.assets_df = pd.DataFrame([
            {'Date': '2026-08-24', 'Symbol': 'AAPL', 'Quantity': 1.0,
             'CostBasis': 100.0, 'ClosingPrice': 150.0, 'Value': 150.0},
        ])

    def _patched(self):
        """Patch every handler class with a recording fake."""
        order = []

        def make(name, attrs=None):
            class Fake:
                def __init__(self, *args, **kwargs):
                    order.append((name, kwargs.get('read_only')))
                    self.history_df = self.assets_df_ref
            Fake.assets_df_ref = self.assets_df
            return Fake

        patches = {
            name: mock.patch.object(daily_update, name, make(name))
            for name in ('AssetHistoryHandler', 'PortfolioHistoryHandler',
                         'AssetHypotheticalHistoryHandler', 'SectorHistoryHandler',
                         'AssetTypeHistoryHandler', 'AccountTypeHistoryHandler',
                         'GeographyHistoryHandler')
        }
        return order, patches

    def test_all_handlers_run_in_write_mode(self):
        order, patches = self._patched()
        for p in patches.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches.values()])

        with mock.patch.object(daily_update, 'table_max_dates',
                               return_value={'assets_history': '2026-08-24'}):
            daily_update.run_update(self.logger)

        names = [n for n, _ in order]
        self.assertEqual(len(names), 7)
        # read_only must be explicitly False for every handler, never None --
        # None would inherit PORTFOLIO_READ_ONLY and silently no-op the updater.
        self.assertTrue(all(ro is False for _, ro in order),
                        f"handlers not all in write mode: {order}")

    def test_asset_history_runs_before_its_dependents(self):
        order, patches = self._patched()
        for p in patches.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches.values()])

        with mock.patch.object(daily_update, 'table_max_dates', return_value={}):
            daily_update.run_update(self.logger)

        names = [n for n, _ in order]
        self.assertLess(names.index('AssetHistoryHandler'),
                        names.index('PortfolioHistoryHandler'))
        self.assertLess(names.index('AssetHistoryHandler'),
                        names.index('AssetHypotheticalHistoryHandler'))

    def test_failure_marks_the_run_failed_and_exits_nonzero(self):
        with mock.patch.object(daily_update, 'gen_history_meta_table'), \
             mock.patch.object(daily_update, 'start_run', return_value=7), \
             mock.patch.object(daily_update, 'run_update',
                               side_effect=RuntimeError('yfinance down')), \
             mock.patch.object(daily_update, 'fail_run') as fail, \
             mock.patch.object(daily_update, 'finish_run') as finish:
            code = daily_update.main([])

        self.assertNotEqual(code, 0)
        fail.assert_called_once()
        self.assertEqual(fail.call_args[0][0], 7)
        finish.assert_not_called()

    def test_success_marks_the_run_successful(self):
        with mock.patch.object(daily_update, 'gen_history_meta_table'), \
             mock.patch.object(daily_update, 'start_run', return_value=9), \
             mock.patch.object(daily_update, 'run_update',
                               return_value={'assets_history': '2026-08-24'}), \
             mock.patch.object(daily_update, 'fail_run') as fail, \
             mock.patch.object(daily_update, 'finish_run') as finish:
            code = daily_update.main([])

        self.assertEqual(code, 0)
        finish.assert_called_once_with(9, {'assets_history': '2026-08-24'})
        fail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.generators.test_daily_update -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.daily_update'`

- [ ] **Step 3: Write `generators/daily_update.py`**

```python
#!/usr/bin/env python
"""
Nightly derived-data updater.

Everything that touches yfinance or writes history lives here, so the Dash web
tier can run with PORTFOLIO_READ_ONLY=1 and never block a page load on a
58-73 second price fetch.

Idempotent: the handlers write with INSERT IGNORE / REPLACE INTO keyed on date,
so re-running after a partial failure is safe.

Usage:
    python generators/daily_update.py
    python generators/daily_update.py --verbose
"""
import argparse
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from libraries.db.history_meta import (gen_history_meta_table, start_run,
                                       finish_run, fail_run, table_max_dates)
from libraries.helpers import aggregate_assets_history_by_symbol
from libraries.HistoryHandlers import (AssetHistoryHandler,
                                       PortfolioHistoryHandler,
                                       AssetHypotheticalHistoryHandler,
                                       SectorHistoryHandler,
                                       AssetTypeHistoryHandler,
                                       AccountTypeHistoryHandler,
                                       GeographyHistoryHandler)

# read_only=False is passed EXPLICITLY everywhere below. Relying on the default
# would inherit PORTFOLIO_READ_ONLY from the environment, and a stray
# PORTFOLIO_READ_ONLY=1 would turn this job into a silent no-op.
DIMENSION_HANDLERS = (
    SectorHistoryHandler,
    AssetTypeHistoryHandler,
    AccountTypeHistoryHandler,
    GeographyHistoryHandler,
)


def run_update(logger) -> dict:
    """
    Bring every derived history table up to date.

    Returns the per-table max dates after the run.
    """
    logger.info("Updating asset history...")
    ah = AssetHistoryHandler(read_only=False)

    # Portfolio and hypothetical history are both derived from asset history,
    # so they must run after it and consume its freshly-updated frame.
    assets_history_df = aggregate_assets_history_by_symbol(ah.history_df)

    logger.info("Updating portfolio history...")
    PortfolioHistoryHandler(assets_history_df=assets_history_df,
                            read_only=False)

    logger.info("Updating hypothetical history...")
    AssetHypotheticalHistoryHandler(assets_history_df=assets_history_df,
                                    read_only=False)

    for handler_class in DIMENSION_HANDLERS:
        logger.info("Updating %s...", handler_class.__name__)
        handler_class(read_only=False)

    return table_max_dates()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verbose', action='store_true',
                        help='Log every handler as it runs')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('daily_update')

    gen_history_meta_table()
    run_id = start_run()

    try:
        tables = run_update(logger)
    except Exception as exc:                      # noqa: BLE001 - top-level job boundary
        logger.exception("Update failed; database keeps its last good data")
        fail_run(run_id, exc)
        return 1

    finish_run(run_id, tables)
    logger.info("Update complete: %s", tables)
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.generators.test_daily_update -v`
Expected: PASS, all four tests.

- [ ] **Step 5: Run it for real and confirm idempotency**

```bash
python generators/daily_update.py --verbose
# Capture row counts, then run a second time and compare.
mysql -u boone -pmysql -h 127.0.0.1 portfolio -e "
  SELECT 'sectors' t, COUNT(*) n FROM sectors_history
  UNION ALL SELECT 'assets', COUNT(*) FROM assets_history
  UNION ALL SELECT 'portfolio', COUNT(*) FROM portfolio_history;"
python generators/daily_update.py --verbose
mysql -u boone -pmysql -h 127.0.0.1 portfolio -e "
  SELECT 'sectors' t, COUNT(*) n FROM sectors_history
  UNION ALL SELECT 'assets', COUNT(*) FROM assets_history
  UNION ALL SELECT 'portfolio', COUNT(*) FROM portfolio_history;"
```
Expected: identical row counts across both runs, exit code 0 both times, and a `success` row in `history_meta`.

- [ ] **Step 6: Commit**

```bash
git add generators/daily_update.py tests/generators/
git commit -m "feat: add generators/daily_update.py scheduled updater

Single idempotent entry point that runs all seven history handlers plus
their dependencies in write mode, recording the run in history_meta.
Passes read_only=False explicitly so a stray PORTFOLIO_READ_ONLY=1 in the
environment cannot silently turn the updater into a no-op.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `current_prices` snapshot

`get_portfolio_current_value()` calls `get_current_price()` → yfinance. This is the last outbound call in the web tier.

**Files:**
- Modify: `libraries/db/sql.py` (append)
- Create: `generators/price_snapshot.py`
- Modify: `libraries/yfinance_helpers/yfinancelib.py:207-216`
- Test: `tests/libraries/test_current_prices.py` (create)

**Interfaces:**
- Consumes: `PORTFOLIO_READ_ONLY` (Task 2).
- Produces:
  - `get_current_price(tickers: list) -> pd.DataFrame` — columns `Symbol`, `Current Price`. Unchanged signature; reads the DB when read-only.
  - `read_current_prices_from_db(tickers: list) -> pd.DataFrame`
  - `generators/price_snapshot.py::main() -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_current_prices.py`:

```python
import unittest
from unittest import mock

import pandas as pd

import libraries.yfinance_helpers.yfinancelib as yfl


class TestGetCurrentPriceReadOnly(unittest.TestCase):

    def test_read_only_reads_db_and_never_calls_yfinance(self):
        db_frame = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 150.0},
            {'Symbol': 'MSFT', 'Current Price': 420.0},
        ])

        def explode(*a, **kw):
            raise AssertionError(
                "yfinance must not be called when PORTFOLIO_READ_ONLY=1")

        with mock.patch.object(yfl, 'PORTFOLIO_READ_ONLY', True), \
             mock.patch.object(yfl, 'read_current_prices_from_db',
                               return_value=db_frame) as read_db, \
             mock.patch.object(yfl, '_gen_current_prices', side_effect=explode):
            out = yfl.get_current_price(['AAPL', 'MSFT'])

        read_db.assert_called_once_with(['AAPL', 'MSFT'])
        self.assertEqual(list(out.columns), ['Symbol', 'Current Price'])
        self.assertEqual(len(out), 2)

    def test_write_mode_still_uses_yfinance(self):
        with mock.patch.object(yfl, 'PORTFOLIO_READ_ONLY', False), \
             mock.patch.object(yfl, '_gen_current_prices',
                               return_value=[{'Symbol': 'AAPL',
                                              'Current Price': 150.0}]) as gen:
            out = yfl.get_current_price(['AAPL'])

        gen.assert_called_once_with(['AAPL'])
        self.assertEqual(out.iloc[0]['Current Price'], 150.0)


class TestSnapshotFallbackToLastClose(unittest.TestCase):
    """
    A symbol with no snapshot row must degrade to its last closing price, not
    to NaN -- otherwise the whole portfolio total silently becomes NaN.
    """

    def test_missing_symbol_falls_back_to_last_close(self):
        snapshot = pd.DataFrame([{'Symbol': 'AAPL', 'Current Price': 150.0}])
        closes = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 148.0},
            {'Symbol': 'NEW',  'Current Price': 20.0},
        ])

        def fake_mysql_to_df(query, columns, cfg, cached=False, verbose=False):
            return snapshot.copy() if 'current_prices' in query else closes.copy()

        with mock.patch.object(yfl, 'mysql_to_df', side_effect=fake_mysql_to_df):
            out = yfl.read_current_prices_from_db(['AAPL', 'NEW'])

        by_symbol = dict(zip(out['Symbol'], out['Current Price']))
        # AAPL has a snapshot, so the snapshot wins over the stale close.
        self.assertEqual(by_symbol['AAPL'], 150.0)
        # NEW has none, so it falls back rather than going missing.
        self.assertEqual(by_symbol['NEW'], 20.0)

    def test_empty_snapshot_falls_back_entirely(self):
        empty = pd.DataFrame(columns=['Symbol', 'Current Price'])
        closes = pd.DataFrame([{'Symbol': 'AAPL', 'Current Price': 148.0}])

        def fake_mysql_to_df(query, columns, cfg, cached=False, verbose=False):
            return empty.copy() if 'current_prices' in query else closes.copy()

        with mock.patch.object(yfl, 'mysql_to_df', side_effect=fake_mysql_to_df):
            out = yfl.read_current_prices_from_db(['AAPL'])

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]['Current Price'], 148.0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_current_prices -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'PORTFOLIO_READ_ONLY'` / `'read_current_prices_from_db'`

- [ ] **Step 3: Add the DDL and query**

Append to `libraries/db/sql.py`:

```python
# current_prices - intraday price snapshot written by generators/price_snapshot.py
# so the read-only web tier never has to call yfinance for a live quote.
create_current_prices_table_sql = \
    ("CREATE TABLE IF NOT EXISTS current_prices ("
     "symbol VARCHAR(16) NOT NULL PRIMARY KEY, "
     "price DECIMAL(18, 4) NOT NULL, "
     "fetched_at DATETIME NOT NULL)")

replace_current_price_sql = \
    ("REPLACE INTO current_prices (symbol, price, fetched_at) "
     "VALUES (%s, %s, %s)")

read_current_prices_query = "SELECT symbol, price FROM current_prices"
read_current_prices_columns = ['Symbol', 'Current Price']

# Fallback for the read-only tier when a symbol has no snapshot yet (first
# deploy, or a symbol bought since the last snapshot run): its most recent
# closing price from assets_history.
read_latest_closing_prices_query = \
    ("SELECT a.symbol, a.closing_price FROM assets_history a "
     "INNER JOIN (SELECT symbol, MAX(date) AS max_date "
     "            FROM assets_history GROUP BY symbol) m "
     "  ON a.symbol = m.symbol AND a.date = m.max_date")
read_latest_closing_prices_columns = ['Symbol', 'Current Price']
```

- [ ] **Step 4: Make `get_current_price` read-only aware**

In `libraries/yfinance_helpers/yfinancelib.py`, add to the imports at the top:

```python
from libraries.globals import PORTFOLIO_READ_ONLY
from libraries.db import dbcfg
from libraries.db.sql import (read_current_prices_query,
                              read_current_prices_columns,
                              read_latest_closing_prices_query,
                              read_latest_closing_prices_columns)
from libraries.pandas_helpers import mysql_to_df
```

Then add `read_current_prices_from_db` and replace the body of `get_current_price` (line 207):

```python
def read_current_prices_from_db(tickers: list) -> pd.DataFrame:
    """
    Read the price snapshot written by generators/price_snapshot.py.

    Any ticker with no snapshot row (first deploy, or bought since the last
    snapshot) falls back to its most recent closing price, so the portfolio
    value degrades to "as of last close" rather than becoming NaN.

    Returns: Symbol, Current Price -- restricted to `tickers`.
    """
    prices_df = mysql_to_df(read_current_prices_query,
                            read_current_prices_columns, dbcfg, cached=False)
    prices_df = prices_df[prices_df['Symbol'].isin(tickers)]

    missing = set(tickers) - set(prices_df['Symbol'])
    if missing:
        closes_df = mysql_to_df(read_latest_closing_prices_query,
                                read_latest_closing_prices_columns,
                                dbcfg, cached=False)
        closes_df = closes_df[closes_df['Symbol'].isin(missing)]
        prices_df = pd.concat([prices_df, closes_df], ignore_index=True)

    return prices_df.reset_index(drop=True)


def get_current_price(tickers: list) -> pd.DataFrame:
    """
    Given list of tickers, return current/realtime price data

    In read-only mode this reads the current_prices snapshot table rather than
    calling yfinance, so the web tier needs no network egress.

    Returns:
        current_prices_df: Symbol, Current Price
    """
    if PORTFOLIO_READ_ONLY:
        return read_current_prices_from_db(tickers)

    current_prices = _gen_current_prices(tickers)

    return pd.DataFrame(current_prices)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_current_prices -v`
Expected: PASS, both tests.

- [ ] **Step 6: Write `generators/price_snapshot.py`**

```python
#!/usr/bin/env python
"""
Intraday price snapshot.

Writes the current price of every held symbol to the current_prices table so
the read-only web tier can show live-ish portfolio value without any outbound
network access. Runs every 15 minutes during market hours -- the same window as
the yfinance memoize cache it replaces, so perceived freshness is unchanged.

Usage:
    python generators/price_snapshot.py
"""
import datetime
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from libraries.db import dbcfg, MysqlDB
from libraries.db.sql import (create_current_prices_table_sql,
                              replace_current_price_sql)
from libraries.helpers import get_portfolio_summary
from libraries.yfinance_helpers import get_current_price


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('price_snapshot')

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
```

Note: `get_current_price` here must run in **write** mode. The systemd unit for this job must not set `PORTFOLIO_READ_ONLY`.

- [ ] **Step 7: Run it and verify the round-trip**

```bash
python generators/price_snapshot.py
PORTFOLIO_READ_ONLY=1 python -c "
import sys; sys.path.insert(0, '.')
from libraries.yfinance_helpers import get_current_price
df = get_current_price(['AAPL', 'MSFT'])
print(df)
"
```
Expected: the snapshot job writes N prices; the read-only read returns them from the DB with no network access.

- [ ] **Step 8: Commit**

```bash
git add libraries/db/sql.py libraries/yfinance_helpers/yfinancelib.py \
        generators/price_snapshot.py tests/libraries/test_current_prices.py
git commit -m "feat: add current_prices snapshot for the read-only web tier

get_current_price() now reads a snapshot table when PORTFOLIO_READ_ONLY=1,
removing the last outbound network call from the web tier. The snapshot is
refreshed every 15 minutes by generators/price_snapshot.py -- the same
window as the yfinance memoize cache it replaces.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Surface staleness in the dashboard

**Files:**
- Modify: `visualization/dash/DashboardHandler.py` (end of `__init__`, ~line 110)
- Modify: `visualization/dash/DemoDashboardHandler.py` (end of `__init__`)
- Modify: `visualization/dash/portfolio_dashboard/portfolio_dashboard.py:70-82`
- Test: `tests/libraries/test_staleness_banner.py` (create)

**Interfaces:**
- Consumes: `compute_staleness`, `last_successful_run` (Task 3).
- Produces:
  - `DashboardHandler.data_as_of: datetime.date | None`
  - `DashboardHandler.is_stale: bool`
  - `build_staleness_banner(data_as_of, is_stale) -> dmc.Alert | None` in `portfolio_dashboard.py`

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_staleness_banner.py`:

```python
import datetime
import unittest

from visualization.dash.portfolio_dashboard.portfolio_dashboard import (
    build_staleness_banner)


class TestStalenessBanner(unittest.TestCase):

    def test_fresh_data_shows_no_banner(self):
        self.assertIsNone(
            build_staleness_banner(datetime.date(2026, 8, 24), is_stale=False))

    def test_stale_data_shows_the_as_of_date(self):
        banner = build_staleness_banner(datetime.date(2026, 8, 18),
                                        is_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('2026-08-18', str(banner.children))

    def test_no_data_at_all_names_the_updater(self):
        banner = build_staleness_banner(None, is_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('daily_update', str(banner.children))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_staleness_banner -v`
Expected: FAIL — `ImportError: cannot import name 'build_staleness_banner'`

- [ ] **Step 3: Add `data_as_of` / `is_stale` to `DashboardHandler`**

In `visualization/dash/DashboardHandler.py`, add to the imports:

```python
from libraries.db.history_meta import compute_staleness, last_successful_run
```

At the very end of `__init__` (after the `_geography_summary_df = None` line):

```python
        ####### DATA FRESHNESS #######
        # Read-only mode never repairs stale data, so the dashboard reports it
        # instead. A missing/failed run reads as stale.
        try:
            self.data_as_of, self._table_dates = last_successful_run()
        except Exception:                          # noqa: BLE001 - never block startup
            self.data_as_of, self._table_dates = None, {}
        self.is_stale = compute_staleness(self.data_as_of)
```

- [ ] **Step 4: Give `DemoDashboardHandler` the same attributes**

`DemoDashboardHandler.__init__` does not call `super().__init__()`, so it needs these set independently. At the end of its `__init__`:

```python
        # Demo data is generated fresh on every run, so it is never stale.
        self.data_as_of = datetime.date.today()
        self._table_dates = {}
        self.is_stale = False
```

Add `import datetime` to that file's imports if not already present.

- [ ] **Step 5: Add the banner builder and wire it in**

In `visualization/dash/portfolio_dashboard/portfolio_dashboard.py`, add above the `_tabs = dmc.Tabs(...)` block:

```python
def build_staleness_banner(data_as_of, is_stale):
    """
    Yellow banner naming the data's as-of date when the updater is behind.

    Returns None when the data is fresh, so the caller can drop it from the
    layout entirely.
    """
    if not is_stale:
        return None

    if data_as_of is None:
        message = ("No portfolio history found. Run "
                   "`python generators/daily_update.py` to populate it.")
    else:
        message = (f"Data as of {data_as_of}. The daily updater has not run "
                   f"since then — run `python generators/daily_update.py` "
                   f"to refresh.")

    return dmc.Alert(message, color="yellow", variant="filled", mb="xs")
```

Then replace the `if os.environ.get('PORTFOLIO_DEMO_MODE') == '1':` block with:

```python
_banners = []

if os.environ.get('PORTFOLIO_DEMO_MODE') == '1':
    _banners.append(dmc.Alert(
        "DEMO MODE — All data is synthetic. No real financial information is displayed.",
        color="orange", variant="filled", mb="xs",
    ))

_staleness_banner = build_staleness_banner(
    DASH_HANDLER.data_as_of, DASH_HANDLER.is_stale)
if _staleness_banner is not None:
    _banners.append(_staleness_banner)

if _banners:
    _content = dmc.Stack(_banners + [_tabs], gap=0)
else:
    _content = _tabs
```

`DASH_HANDLER` must be imported **after** the tab imports, never at the top of the file:

```python
# Import position matters. globals.py instantiates DASH_HANDLER at import time
# and branches on PORTFOLIO_DEMO_MODE, which portfolio_dashboard.py sets from
# --demo at the very top. Importing this above the tab imports would construct
# the real (non-demo) handler and silently break `--demo`.
from visualization.dash.portfolio_dashboard.globals import DASH_HANDLER
```

Place it immediately after the existing `from visualization.dash.portfolio_dashboard.tabs.chat_tab import chat_tab` line.

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_staleness_banner -v`
Expected: PASS, all three tests.

- [ ] **Step 7: Verify both modes still boot**

```bash
timeout 60 python visualization/dash/portfolio_dashboard/portfolio_dashboard.py --demo &
sleep 25 && curl -s -o /dev/null -w "demo HTTP %{http_code}\n" http://localhost:8050
kill %1
```
Expected: `demo HTTP 200`, no banner about staleness (demo is never stale).

- [ ] **Step 8: Commit**

```bash
git add visualization/dash/DashboardHandler.py \
        visualization/dash/DemoDashboardHandler.py \
        visualization/dash/portfolio_dashboard/portfolio_dashboard.py \
        tests/libraries/test_staleness_banner.py
git commit -m "feat: surface data staleness as a dashboard banner

Read-only mode serves stale data rather than blocking to repair it, so the
dashboard now names the as-of date when the updater is behind, and points
at daily_update.py when no history exists at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Bound `_aggregation_cache`

An unbounded module-level dict keyed by `(symbols, cadence, start_date, account_type)`. In a long-running hosted process with varying filters it grows without limit.

**Files:**
- Modify: `libraries/globals.py`
- Modify: `libraries/helpers.py:539-579`
- Test: `tests/libraries/test_aggregation_cache.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_aggregation_cache` becomes a `collections.OrderedDict` with LRU eviction at `AGGREGATION_CACHE_MAX_ENTRIES`.

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_aggregation_cache.py`:

```python
import unittest
from unittest import mock

import pandas as pd

import libraries.helpers as helpers


class TestAggregationCacheBounding(unittest.TestCase):

    def setUp(self):
        helpers._aggregation_cache.clear()
        self.addCleanup(helpers._aggregation_cache.clear)

    def _fake_expanded(self):
        return pd.DataFrame([
            {'Date': '2026-08-24', 'Sector': 'Technology',
             'Value': 100.0, 'CostBasis': 80.0},
        ])

    def test_cache_never_exceeds_the_configured_maximum(self):
        max_entries = helpers.AGGREGATION_CACHE_MAX_ENTRIES

        with mock.patch.object(helpers, 'gen_assets_historical_value',
                               return_value=pd.DataFrame()), \
             mock.patch.object(helpers, 'add_asset_info',
                               return_value=self._fake_expanded()):
            for i in range(max_entries + 5):
                helpers.gen_aggregated_historical_value(
                    dimension='Sector', start_date=f'2026-01-{i % 28 + 1:02d}',
                    account_type=f'acct-{i}')

        self.assertLessEqual(len(helpers._aggregation_cache), max_entries)

    def test_recently_used_entry_survives_eviction(self):
        max_entries = helpers.AGGREGATION_CACHE_MAX_ENTRIES

        with mock.patch.object(helpers, 'gen_assets_historical_value',
                               return_value=pd.DataFrame()), \
             mock.patch.object(helpers, 'add_asset_info',
                               return_value=self._fake_expanded()):
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='keep-me')

            for i in range(max_entries - 1):
                helpers.gen_aggregated_historical_value(
                    dimension='Sector', account_type=f'filler-{i}')

            # Touch the first entry so it becomes most-recently-used...
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='keep-me')

            # ...then push one more in, which must evict a filler, not 'keep-me'.
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='overflow')

        keys = [k for k in helpers._aggregation_cache if 'keep-me' in str(k)]
        self.assertEqual(len(keys), 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_aggregation_cache -v`
Expected: FAIL — `AttributeError: module 'libraries.helpers' has no attribute 'AGGREGATION_CACHE_MAX_ENTRIES'`

- [ ] **Step 3: Add the bound**

In `libraries/globals.py`, after the `PORTFOLIO_READ_ONLY` line:

```python
# Upper bound on libraries.helpers._aggregation_cache. Each entry is a fully
# expanded per-asset history frame, so this trades memory for recompute.
AGGREGATION_CACHE_MAX_ENTRIES = 8
```

In `libraries/helpers.py`, add `from collections import OrderedDict` to the imports and `AGGREGATION_CACHE_MAX_ENTRIES` to the `libraries.globals` import. Replace the `_aggregation_cache = {}` definition (line 541) with:

```python
# LRU-bounded. Previously an unbounded dict: in a long-running hosted process
# with varying filter parameters it grew without limit.
_aggregation_cache = OrderedDict()
```

Then in `gen_aggregated_historical_value`, replace the cache read/write block (lines 568-579) with:

```python
    # Check cache for the expensive expanded_df (shared across dimension calls)
    cache_key = (tuple(symbols), cadence, str(start_date), account_type)
    if cache_key in _aggregation_cache:
        _aggregation_cache.move_to_end(cache_key)
    else:
        # Get all assets' historical values
        assets_history_df = gen_assets_historical_value(symbols=symbols,
                                                        cadence=cadence,
                                                        start_date=start_date,
                                                        include_exit_date=False,
                                                        account_type=account_type)
        # Add in Sector, AssetType, etc columns
        _aggregation_cache[cache_key] = add_asset_info(assets_history_df,
                                                       truncate=False)
        while len(_aggregation_cache) > AGGREGATION_CACHE_MAX_ENTRIES:
            _aggregation_cache.popitem(last=False)

    expanded_df = _aggregation_cache[cache_key]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_aggregation_cache -v`
Expected: PASS, both tests.

- [ ] **Step 5: Commit**

```bash
git add libraries/globals.py libraries/helpers.py tests/libraries/test_aggregation_cache.py
git commit -m "fix: bound _aggregation_cache with LRU eviction

Was an unbounded module-level dict keyed by filter parameters, which grows
without limit in a long-running hosted process.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Payload downsampling

Current payloads: Hypotheticals 6.42 MB / 264,843 pts / 126 traces; Assets 1.53 MB; Sectors 1.00 MB. Target: under 500 KB per tab.

**Files:**
- Create: `libraries/downsample.py`
- Modify: `libraries/globals.py`
- Modify: `visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py:88-105`
- Modify: `visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py:39-67`
- Test: `tests/libraries/test_downsample.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `downsample_history(df, date_col='Date', group_cols=(), window_days=None, today=None) -> pd.DataFrame`
  - `top_n_symbols(df, symbol_col='Symbol', value_col='ClosingPrice % Change', n=10) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_downsample.py`:

```python
import datetime
import unittest

import pandas as pd

from libraries.downsample import downsample_history, top_n_symbols


class TestDownsampleHistory(unittest.TestCase):

    def setUp(self):
        self.today = datetime.date(2026, 8, 24)
        # Three years of daily data for two symbols.
        dates = pd.date_range(end=pd.Timestamp(self.today),
                              periods=365 * 3, freq='D').date
        rows = []
        for symbol, base in (('AAA', 100.0), ('BBB', 50.0)):
            for i, d in enumerate(dates):
                rows.append({'Date': d, 'Symbol': symbol, 'Value': base + i})
        self.df = pd.DataFrame(rows)

    def test_recent_window_keeps_daily_resolution(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        cutoff = self.today - datetime.timedelta(days=365)
        recent_in = len(self.df[self.df['Date'] >= cutoff])
        recent_out = len(out[out['Date'] >= cutoff])
        self.assertEqual(recent_out, recent_in)

    def test_old_data_is_thinned(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        self.assertLess(len(out), len(self.df))
        # Weekly beyond a year: roughly 1/7th of the old rows survive.
        cutoff = self.today - datetime.timedelta(days=365)
        old_in = len(self.df[self.df['Date'] < cutoff])
        old_out = len(out[out['Date'] < cutoff])
        self.assertLess(old_out, old_in / 3)

    def test_first_and_last_point_per_group_are_preserved(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        for symbol in ('AAA', 'BBB'):
            original = self.df[self.df['Symbol'] == symbol]
            kept = out[out['Symbol'] == symbol]
            self.assertEqual(kept['Date'].min(), original['Date'].min())
            self.assertEqual(kept['Date'].max(), original['Date'].max())
            self.assertEqual(
                kept.loc[kept['Date'].idxmin(), 'Value'],
                original.loc[original['Date'].idxmin(), 'Value'])
            self.assertEqual(
                kept.loc[kept['Date'].idxmax(), 'Value'],
                original.loc[original['Date'].idxmax(), 'Value'])

    def test_short_history_is_returned_unchanged(self):
        recent = self.df[self.df['Date'] >=
                         self.today - datetime.timedelta(days=30)]
        out = downsample_history(recent, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        self.assertEqual(len(out), len(recent))

    def test_empty_frame_passes_through(self):
        empty = pd.DataFrame(columns=['Date', 'Symbol', 'Value'])
        self.assertTrue(downsample_history(empty, group_cols=('Symbol',)).empty)


class TestTopNSymbols(unittest.TestCase):

    def test_picks_largest_absolute_movers(self):
        df = pd.DataFrame([
            {'Symbol': 'FLAT', 'ClosingPrice % Change': 0.5},
            {'Symbol': 'UP',   'ClosingPrice % Change': 90.0},
            {'Symbol': 'DOWN', 'ClosingPrice % Change': -80.0},
            {'Symbol': 'MEH',  'ClosingPrice % Change': 2.0},
        ])
        out = top_n_symbols(df, n=2)
        self.assertEqual(set(out), {'UP', 'DOWN'})

    def test_n_larger_than_available_returns_everything(self):
        df = pd.DataFrame([{'Symbol': 'ONLY', 'ClosingPrice % Change': 1.0}])
        self.assertEqual(top_n_symbols(df, n=10), ['ONLY'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.libraries.test_downsample -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libraries.downsample'`

- [ ] **Step 3: Add the config value**

In `libraries/globals.py`, after `AGGREGATION_CACHE_MAX_ENTRIES`:

```python
# Chart payload shaping. Beyond this many days back, history is thinned to
# weekly: a 40k-point trace carries far more points than the ~1500 horizontal
# pixels a chart actually has, so the difference is invisible.
DOWNSAMPLE_DAILY_WINDOW_DAYS = 365
# How many series the Hypotheticals/Assets charts show before the user opts in
# to more via the existing dropdowns.
DEFAULT_CHART_SERIES = 10
```

- [ ] **Step 4: Write `libraries/downsample.py`**

```python
#!/usr/bin/env python
"""
Chart payload shaping.

Pure functions -- no DB, no network -- so they can be unit tested directly and
called from any Dash callback.
"""
import datetime
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from libraries.globals import DOWNSAMPLE_DAILY_WINDOW_DAYS, DEFAULT_CHART_SERIES


def downsample_history(df: pd.DataFrame, date_col: str = 'Date',
                       group_cols=(), window_days: int = None,
                       today: datetime.date = None) -> pd.DataFrame:
    """
    Keep daily resolution inside `window_days` of today; weekly (Fridays)
    before that. Each group's first and last points are always preserved so
    endpoints and totals still line up.

    group_cols: e.g. ('Symbol',) or ('Sector',). Empty for single-series data.
    today:      injectable for tests.
    """
    if df.empty:
        return df

    if window_days is None:
        window_days = DOWNSAMPLE_DAILY_WINDOW_DAYS
    if today is None:
        today = datetime.date.today()

    group_cols = list(group_cols)
    cutoff = pd.Timestamp(today - datetime.timedelta(days=window_days))
    dates = pd.to_datetime(df[date_col])

    old = df[dates < cutoff]
    if old.empty:
        return df

    recent = df[dates >= cutoff]
    old_dates = pd.to_datetime(old[date_col])

    # Fridays only, beyond the daily window.
    keep = old[old_dates.dt.weekday == 4]

    # Always retain each group's endpoints, otherwise a series can start or end
    # at a different value than the full-resolution data.
    if group_cols:
        first = old.loc[old.groupby(group_cols)[date_col].idxmin()]
        last = old.loc[old.groupby(group_cols)[date_col].idxmax()]
    else:
        first = old.loc[[old[date_col].idxmin()]]
        last = old.loc[[old[date_col].idxmax()]]

    keep = pd.concat([keep, first, last])
    keep = keep[~keep.index.duplicated(keep='first')]

    out = pd.concat([keep, recent])
    out = out.sort_values(group_cols + [date_col])
    return out.reset_index(drop=True)


def top_n_symbols(df: pd.DataFrame, symbol_col: str = 'Symbol',
                  value_col: str = 'ClosingPrice % Change',
                  n: int = None) -> list:
    """
    The n symbols with the largest absolute value in `value_col`.

    Used to pick a default subset for charts that would otherwise ship every
    series (Hypotheticals renders 126 traces / 6.4 MB by default).
    """
    if df.empty:
        return []
    if n is None:
        n = DEFAULT_CHART_SERIES

    extremes = df.groupby(symbol_col)[value_col].apply(
        lambda s: s.abs().max())
    return extremes.sort_values(ascending=False).head(n).index.tolist()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.libraries.test_downsample -v`
Expected: PASS, all seven tests.

- [ ] **Step 6: Wire downsampling into the dimension tabs**

In `visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py`, add to the imports:

```python
from libraries.downsample import downsample_history
```

In `update_tab`, immediately before `fig = px.line(...)` (after the `chart_df['y']` assignment in both branches), insert:

```python
        # Thin the payload before serialization. The table above is computed
        # from the FULL frame, so only the chart is affected.
        chart_df = downsample_history(chart_df, group_cols=(column_name,))
```

- [ ] **Step 7: Wire downsampling and the top-N default into Hypotheticals**

In `visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py`, add to the imports:

```python
from libraries.downsample import downsample_history, top_n_symbols
```

In `initialize_hypotheticals_tab`, replace the `fig = px.line(...)` block with:

```python
    # The initial view defaults to the biggest movers instead of all 126
    # series (6.4 MB). The sector/asset dropdowns below bring in the rest.
    default_symbols = top_n_symbols(normalized_hypo_df)
    initial_df = normalized_hypo_df[
        normalized_hypo_df['Symbol'].isin(default_symbols)]
    initial_df = downsample_history(initial_df, group_cols=('Symbol',))

    fig = px.line(
        initial_df,
        x=initial_df['Date'],
        y=initial_df['ClosingPrice % Change'],
        color=initial_df['Symbol'],
        line_dash=initial_df['Sector'],
    )
    fig.update_layout(height=800)
    fig.update_yaxes(ticksuffix="%")
```

In `update_normalized_hypo_graph`, insert immediately before `normalized_hypo_fig = px.line(...)`:

```python
    df = downsample_history(df, group_cols=('Symbol',))
```

- [ ] **Step 8: Measure the payloads and confirm the target**

```bash
python -c "
import sys; sys.path.insert(0, '.')
import plotly.express as px
from visualization.dash.DashboardHandler import DashboardHandler
from libraries.downsample import downsample_history, top_n_symbols

dh = DashboardHandler()
for label, attr, col in [('sectors','sectors_history_df','Sector'),
                         ('asset_types','asset_types_history_df','AssetType'),
                         ('geography','geography_history_df','Geography')]:
    d = getattr(dh, attr).copy(); d['y'] = d['TotalValue']
    d = downsample_history(d, group_cols=(col,))
    fig = px.line(d, x=d['Date'], y=d['y'], color=d[col])
    print(f'{label:<14} pts={len(d):>7} {len(fig.to_json())/1e6:.2f} MB')

h = dh.expand_history_df(dh.exits_hypotheticals_history_df)
h = h[h['Symbol'].isin(top_n_symbols(h))]
h = downsample_history(h, group_cols=('Symbol',))
fig = px.line(h, x=h['Date'], y=h['ClosingPrice % Change'], color=h['Symbol'])
print(f'{\"hypotheticals\":<14} pts={len(h):>7} {len(fig.to_json())/1e6:.2f} MB')
"
```
Expected: every tab under 0.5 MB. Record the numbers in the commit message.

- [ ] **Step 9: Run the full suite**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add libraries/downsample.py libraries/globals.py \
        visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py \
        visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py \
        tests/libraries/test_downsample.py
git commit -m "perf: downsample chart payloads and default to top-N series

Weekly resolution beyond one year (endpoints always preserved), and the
Hypotheticals chart now opens on the biggest movers rather than all 126
series. Targets <500 KB per tab, down from 6.42 MB / 1.53 MB / 1.00 MB.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Scheduling units and documentation

**Files:**
- Create: `deploy/systemd/wake-daily-update.service`, `wake-daily-update.timer`
- Create: `deploy/systemd/wake-price-snapshot.service`, `wake-price-snapshot.timer`
- Create: `deploy/README.md`
- Modify: `README.md`, `CLAUDE.md`
- Modify: `PERFORMANCE_ANALYSIS.md`, `FRAMEWORK_COMPARISON.md`, `OPTIMIZATION.md`, `QUICK_WINS_IMPLEMENTED.md`, `MAJOR_OPTIMIZATIONS_IMPLEMENTED.md`

**Interfaces:**
- Consumes: `generators/daily_update.py` (Task 4), `generators/price_snapshot.py` (Task 5).
- Produces: no code interfaces — deployment artifacts and docs.

- [ ] **Step 1: Write the daily updater units**

`deploy/systemd/wake-daily-update.service`:

```ini
[Unit]
Description=Wake portfolio daily history update
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/kineticrick/code/python/wake
# Deliberately NOT read-only: this is the write tier.
Environment=PORTFOLIO_READ_ONLY=0
ExecStart=/home/kineticrick/code/python/wake/venv/bin/python generators/daily_update.py --verbose
TimeoutStartSec=1800
```

`deploy/systemd/wake-daily-update.timer`:

```ini
[Unit]
Description=Run Wake daily history update after US market close

[Timer]
# 17:30 America/New_York, weekdays. Market closes at 16:00 ET; the delay gives
# yfinance time to settle the day's closing prices.
OnCalendar=Mon-Fri 17:30 America/New_York
# Catch up after a missed run (laptop asleep) instead of skipping the day.
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write the price snapshot units**

`deploy/systemd/wake-price-snapshot.service`:

```ini
[Unit]
Description=Wake intraday price snapshot
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/kineticrick/code/python/wake
Environment=PORTFOLIO_READ_ONLY=0
ExecStart=/home/kineticrick/code/python/wake/venv/bin/python generators/price_snapshot.py
TimeoutStartSec=300
```

`deploy/systemd/wake-price-snapshot.timer`:

```ini
[Unit]
Description=Refresh Wake current-price snapshot every 15 minutes during market hours

[Timer]
OnCalendar=Mon-Fri 09:30..16:00/15 America/New_York
Persistent=false
AccuracySec=30s

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write `deploy/README.md`**

```markdown
# Deployment

## Scheduled jobs

Wake splits into a **write tier** (scheduled jobs that fetch prices and compute
history) and a **read-only web tier** (the Dash app). The web tier makes no
outbound network calls and no database writes.

Install the user timers:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wake-daily-update.timer
systemctl --user enable --now wake-price-snapshot.timer

# Let timers run even when you are not logged in
sudo loginctl enable-linger "$USER"
```

Check status and logs:

```bash
systemctl --user list-timers 'wake-*'
journalctl --user -u wake-daily-update.service -n 50
```

Force a run:

```bash
systemctl --user start wake-daily-update.service
# or directly:
python generators/daily_update.py --verbose
```

## Running the web tier read-only

```bash
PORTFOLIO_READ_ONLY=1 python visualization/dash/portfolio_dashboard/portfolio_dashboard.py
```

If the updater has not run, the dashboard serves the last good data and shows a
banner naming the as-of date. It never blocks to fetch prices itself.

**Note:** paths in the unit files are absolute and assume the repo lives at
`/home/kineticrick/code/python/wake`. Update them if you deploy elsewhere.
```

- [ ] **Step 4: Update `README.md` and `CLAUDE.md`**

Add to the "Data Import" / development-commands section of **both** files:

```markdown
### Scheduled Updates

```bash
# Bring every derived history table up to date (yfinance + DB writes).
# This is what the systemd timer runs after market close.
python generators/daily_update.py --verbose

# Refresh the intraday current-price snapshot
python generators/price_snapshot.py
```

### Running the dashboard read-only

```bash
PORTFOLIO_READ_ONLY=1 python visualization/dash/portfolio_dashboard/portfolio_dashboard.py
```

In read-only mode the app never calls yfinance and never writes to the
database — history is kept current by the scheduled jobs above. This is the
mode to use when hosting. See `deploy/README.md`.
```

- [ ] **Step 5: Mark the superseded performance docs**

Add this block to the top of each of `PERFORMANCE_ANALYSIS.md`, `FRAMEWORK_COMPARISON.md`, `OPTIMIZATION.md`, `QUICK_WINS_IMPLEMENTED.md`, and `MAJOR_OPTIMIZATIONS_IMPLEMENTED.md`, directly under the existing H1:

```markdown
> **SUPERSEDED (2026-08-24).** Measurements in this document did not hold up
> under profiling. The dominant cost was never rendering or DataFrame work — it
> was a synchronous 58–73 second yfinance fetch running inside a Dash callback.
> The `aggregate_assets_history_by_symbol` "~20s hotspot" was cProfile overhead;
> the real figure is 0.83s. Client-side rendering blocks the main thread for 0ms
> (Plotly already uses WebGL). A framework migration is not warranted.
>
> See `docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md`.
```

- [ ] **Step 6: Verify the units parse**

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/wake-daily-update.service
systemctl --user list-timers 'wake-*' --all
```
Expected: `daemon-reload` and `verify` produce no errors; both timers appear.

- [ ] **Step 7: End-to-end verification**

```bash
# 1. Write tier populates everything
python generators/daily_update.py --verbose
python generators/price_snapshot.py

# 2. Read-only web tier starts fast and serves
PORTFOLIO_READ_ONLY=1 timeout 90 \
  python visualization/dash/portfolio_dashboard/portfolio_dashboard.py &
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8050
kill %1
```
Expected: HTTP 200, startup well under the pre-change time, no staleness banner right after a successful update.

- [ ] **Step 8: Commit**

```bash
git add deploy/ README.md CLAUDE.md PERFORMANCE_ANALYSIS.md \
        FRAMEWORK_COMPARISON.md OPTIMIZATION.md \
        QUICK_WINS_IMPLEMENTED.md MAJOR_OPTIMIZATIONS_IMPLEMENTED.md
git commit -m "docs: add systemd units and document the read-only web tier

Adds user timers for daily_update (weekdays 17:30 ET, Persistent=true) and
price_snapshot (every 15m during market hours), plus deploy/README.md.

Marks the older performance docs superseded: their headline figures were
cProfile artifacts and they point at a framework migration the measurements
do not support.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Checklist

After all tasks:

- [ ] `python -m unittest discover -s tests -p "test_*.py"` passes
- [ ] `PORTFOLIO_READ_ONLY=1` dashboard start makes zero yfinance calls (verify: disconnect network, confirm the app still boots and serves)
- [ ] Opening every tab in read-only mode is sub-second, even with all history tables 40+ days stale
- [ ] Every tab's chart payload is under 500 KB
- [ ] `python generators/daily_update.py` twice in a row leaves row counts unchanged
- [ ] A deliberately failed run (e.g. break the DB password) leaves `history_meta.status='failed'`, exits non-zero, and the dashboard shows the staleness banner rather than an error page
- [ ] Demo mode (`--demo`) still works and shows no staleness banner
