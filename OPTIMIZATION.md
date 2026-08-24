# Portfolio Analysis: Optimization & Improvement Analysis

> **SUPERSEDED (2026-08-24).** Measurements in this document did not hold up
> under profiling. The dominant cost was never rendering or DataFrame work — it
> was a synchronous 58–73 second yfinance fetch running inside a Dash callback.
> The `aggregate_assets_history_by_symbol` "~20s hotspot" was cProfile overhead;
> the real figure is 0.83s. Client-side rendering blocks the main thread for 0ms
> (Plotly already uses WebGL). A framework migration is not warranted.
>
> See `docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md`.

*Analysis performed February 2026 using Claude Opus 4.6*

---

## Table of Contents

- [Part 1: Performance Optimizations](#part-1-performance-optimizations)
  - [Completed](#completed)
  - [Remaining: Medium Impact](#remaining-medium-impact)
- [Part 2: Visualization / UX Improvements](#part-2-visualization--ux-improvements)
  - [Architectural Issues](#architectural-issues)
  - [UX Gaps](#ux-gaps)
  - [Missing Features](#missing-features)
  - [Quick Wins](#quick-wins)
- [Part 3: Framework Assessment](#part-3-framework-assessment)
  - [Framework Comparison](#framework-comparison)
  - [Recommendation](#recommendation)
  - [Improvements Within Dash](#improvements-within-dash)

---

## Part 1: Performance Optimizations

### Completed

#### Batch INSERTs for History Handlers
**Commit:** `d72a7aa` | **Impact:** 5-20x faster history writes

Converted 5 history handlers from row-by-row `iterrows()` + `db.execute()` to `executemany()` batch inserts:
- `SectorHistoryHandler`
- `AssetTypeHistoryHandler`
- `AccountTypeHistoryHandler`
- `GeographyHistoryHandler`
- `AssetHypotheticalHistoryHandler`

These now match the pattern already used by `AssetHistoryHandler` and `PortfolioHistoryHandler`.

#### Database Indexes
**Commit:** `d72a7aa` | **Impact:** 2-5x faster queries

Added 11 secondary indexes across transaction and history tables to eliminate full table scans:

| Table | Index | Speeds Up |
|-------|-------|-----------|
| `trades` | `idx_trades_symbol` | `build_master_log()` symbol filtering |
| `trades` | `idx_trades_symbol_date` | Date-range + symbol queries |
| `dividends` | `idx_dividends_symbol` | Dividend lookups by symbol |
| `splits` | `idx_splits_symbol` | Split lookups by symbol |
| `entities` | `idx_entities_symbol` | `add_asset_info()` joins |
| `assets_history` | `idx_assets_history_symbol` | Symbol-only queries on history |
| `assets_hypothetical_history` | `idx_assets_hypo_history_symbol` | Hypothetical symbol lookups |
| `sectors_history` | `idx_sectors_history_sector` | Sector dimension filtering |
| `asset_types_history` | `idx_asset_types_history_type` | Asset type filtering |
| `account_types_history` | `idx_account_types_history_type` | Account type filtering |
| `geography_history` | `idx_geography_history_geo` | Geography filtering |

Indexes are created idempotently via `MysqlDB.create_index_safe()` (catches MySQL error 1061 for duplicate index names). Transaction table indexes are applied by `importer.py`; history table indexes are applied by `BaseHistoryHandler.gen_table()`.

---

#### `gen_hist_quantities()` O(n²) Fix
**Commit:** `3979fb4` | **Impact:** 3-5x faster iteration

Replaced `iterrows()` with `itertuples(index=False)` for faster row iteration. Replaced `purchase_list.sort()` (O(n log n) on every buy) with `bisect.insort()` (O(log n) per insert).

#### Aggregation Cache for Dimension Handlers
**Commit:** `3979fb4` | **Impact:** ~4x faster dimension summary generation

Added module-level `_aggregation_cache` in `gen_aggregated_historical_value()` so the expensive `gen_assets_historical_value()` + `add_asset_info()` computation is done once and reused across all 4 dimension handlers (Sector, AssetType, AccountType, Geography).

#### Entity Data Cache in `add_asset_info()`
**Commit:** `3979fb4` | **Impact:** Eliminates 5+ redundant DB deserializations per session

Added module-level `_entities_df_cache` and `_get_entities_df()` helper. Entities table is loaded from DB once per session instead of on every call.

#### `build_master_log()` Cleanup
**Commit:** `3979fb4` | **Impact:** ~15-20% faster

Replaced `globals()` lookups with static `_EVENT_QUERIES` dict. Replaced concat-in-loop with collect-then-concat pattern.

#### `_gen_summary_df()` Single GroupBy
**Commit:** `3979fb4` | **Impact:** Minor cleanup

Replaced double-groupby (sum + mean + merge) with single `.agg()` call.

#### Dimension Handler `set_history()` Return Values
**Commit:** `3979fb4` | **Impact:** Eliminates redundant DB reads

Added `return self.get_history()` to all 4 dimension handlers so `BaseHistoryHandler` can use the result instead of re-reading from DB after writes.

#### `_add_pct_change()` Collect-then-Concat
**Commit:** `3979fb4` | **Impact:** Minor cleanup

Replaced concat-in-loop with collect-then-concat pattern.

#### Vectorized `gen_historical_stats()`
**Commit:** `cbea73a` | **Impact:** 3-5x faster

Replaced per-symbol loop (300+ DataFrame operations for 100+ assets) with vectorized `groupby('Symbol')['ClosingPrice'].agg(first, last, max)` for both actuals and hypotheticals.

#### Connection Pool Size Increase
**Commit:** `cbea73a` | **Impact:** Reduces connection wait times

Increased MySQL connection pool from 5 to 10 concurrent connections.

#### yfinance Cache Expiration Tuning
**Commit:** `cbea73a` | **Impact:** Better cache behavior

Historical prices: 12h → 24h (stable data, rarely changes). Current prices: 1h → 15min (more relevant for intraday).

#### UX Quick Wins
**Commit:** (round 4) | **Impact:** Improved usability

- Default tab changed from Assets to Portfolio (`portfolio_dashboard.py`)
- Fixed `port_hist_df['Value'][0]` → `port_hist_df['Value'].iloc[0]` bug (`portfolio_tab.py`)
- Removed hardcoded `paper_bgcolor='lightblue'` (`portfolio_tab.py`)
- Added `dcc.Loading` wrapper around tabs for loading state feedback (`portfolio_dashboard.py`)
- Added `rangeslider` to portfolio history chart (`portfolio_tab.py`)

#### Replace DataTables with dash-ag-grid + Dimension Tab Factory
**Commit:** `fabc329` | **Impact:** -657 lines, eliminates global state, fixes bugs

Replaced all `dash_table.DataTable` components with `dag.AgGrid` across every tab. ag-grid's `selectedRows` returns actual row data (not indices), eliminating the need for `row_symbol_mapping` / `row_sector_mapping` globals and the fragile table-update callbacks that maintained them.

Created `dimension_tab_factory.py` — a factory function `create_dimension_tab()` that generates all 4 dimension tabs (sectors, asset_types, account_types, geography) from 4 parameters. Each dimension tab file is now ~8 lines instead of ~177.

- **Global state removed:** Zero `global` keywords remain in tab files
- **Duplicate code eliminated:** 4 copy-pasted dimension tabs → 1 factory + 4 one-line calls
- **Bug fixed:** `geography_tab.py` had copy-paste bug using `row_account_type_mapping` instead of `row_geography_mapping`
- **Callbacks simplified:** Dimension tabs reduced from 2 callbacks (table update + graph update) to 1 (graph update only — ag-grid handles sorting/selection natively)
- **Assets tab:** Dropdown filtering outputs `rowData` directly; graph callback reads `selectedRows` for symbols
- **Portfolio tab:** 3 DataTables (milestones, winners, losers) replaced with AgGrids using `domLayout: autoHeight`
- **Hypotheticals tab:** Static stats DataTable replaced with AgGrid

---

### Remaining: Medium Impact

#### 1. Asset History Loaded for ALL Ever-Owned Assets
**File:** `visualization/dash/DashboardHandler.py:41`

**Status:** Investigated, deferred. `AssetHypotheticalHistoryHandler` requires full asset history (including exited assets) for hypothetical analysis. Restricting `AssetHistoryHandler` to portfolio symbols would require a second handler for exited assets, adding complexity with minimal gain since DB reads are cached and Python-level filtering is cheap (~30K extra rows out of 110K).

#### 2. Calendar Days Instead of Business Days
**File:** `libraries/yfinance_helpers/yfinancelib.py:143`

**Status:** Deferred. Requires coordinated changes across `gen_hist_quantities()` (which also uses calendar days), price/quantity merge logic, and milestone date lookups to avoid breaking weekend date references. Risk outweighs the ~30% row reduction benefit.

---

## Part 2: Visualization / UX Improvements

### Architectural Issues

#### ~~1. Global Mutable State in Callbacks~~
~~**Files:** `tabs/assets_tab.py:55`, `tabs/sectors_tab.py`, etc.~~

~~Tabs use `global assets_table_df, row_symbol_mapping` — mutating shared state in callbacks. This is fragile and will break with concurrent users.~~

**Status:** Done (`fabc329`). Eliminated entirely by switching to dash-ag-grid — `selectedRows` returns actual row data, so row mappings and global state are no longer needed.

#### ~~2. All Tabs Load at Startup~~
~~**Files:** `tabs/__init__.py`, `globals.py`~~

~~Every tab executes module-level code at import time, even tabs the user never visits. `DashboardHandler.__init__()` precomputes everything. No lazy loading — the user stares at a blank screen during initialization.~~

**Status:** Done. `DashboardHandler` now uses lazy `@property` methods for 5 handlers (Hypotheticals, Sector, AssetType, AccountType, Geography). Dimension tabs and hypotheticals tab defer all data access into callbacks gated by the active tab ID. Only Asset + Portfolio handlers load at startup.

#### ~~3. Four Dimension Tabs are 90% Duplicate Code~~
~~**Files:** `tabs/sectors_tab.py`, `tabs/asset_types_tab.py`, `tabs/account_types_tab.py`, `tabs/geography_tab.py`~~

~~Nearly identical code maintained as separate files. A bug fix must be replicated 4 times.~~

**Status:** Done (`fabc329`). Refactored into `dimension_tab_factory.py`. Each tab file is now a single factory call (~8 lines). Also fixed a copy-paste bug in `geography_tab.py`.

### UX Gaps

#### 4. No Loading States
No `dcc.Loading` wrapper or skeleton screens. Users see nothing during initialization.

#### 5. Filter Logic is AND Instead of OR
**File:** `tabs/assets_tab.py:59-93`

Selecting a sector AND an asset type returns only assets matching BOTH, rather than the union. Counter-intuitive for exploratory filtering.

#### 6. No Data Freshness Indicator
No "last updated" timestamp, no refresh button. Data goes stale silently.

#### 7. Default Tab is Assets, Not Portfolio
**File:** `portfolio_dashboard.py:52`

`active_tab='assets-dash-tab'` — Portfolio overview should be the landing page.

#### 8. Chart Cluttering
No limit on selected assets for charting. Selecting 50+ assets makes the chart unreadable. Needs a warning or limit.

#### 9. Portfolio Tab Bug
**File:** `tabs/portfolio_tab.py:38`

Uses `port_hist_df['Value'][0]` instead of `port_hist_df['Value'].iloc[0]` — fragile if index isn't 0-based.

#### 10. Hardcoded Background Color
**File:** `tabs/portfolio_tab.py:109`

`paper_bgcolor = "lightblue"` — inconsistent with Bootstrap theme, not responsive to dark mode.

### Missing Features

- **No cross-filtering** between tabs (click sector -> filter assets)
- **No export** (CSV/Excel for tables, PNG for charts)
- **No benchmark comparison** (vs S&P 500, Russell 2000, etc.)
- **No secondary Y-axis** (absolute value + % change on same chart)
- **No date range slider** on charts (`rangeslider={'visible': True}`)
- **No conditional coloring** (green/red for positive/negative returns in tables)
- ~~**No keyboard navigation** — DataTables require mouse for row selection (accessibility issue)~~ — Improved by ag-grid migration (`fabc329`)

### Quick Wins

| Fix | Where | Status |
|-----|-------|--------|
| ~~Change default tab to Portfolio~~ | `portfolio_dashboard.py` | Done |
| ~~Add `dcc.Loading` wrapper~~ | `portfolio_dashboard.py` | Done |
| ~~Format Y-axis as percentage~~ | 6 tab files (7 charts) | Done |
| ~~Use `iloc[0]` not `[0]`~~ | `portfolio_tab.py` | Done |
| ~~Replace `paper_bgcolor='lightblue'`~~ | `portfolio_tab.py` | Done |
| ~~Add `rangeslider`~~ | `portfolio_tab.py` | Done |

---

## Part 3: Framework Assessment

### Framework Comparison

| Framework | Charts | Tables | Reactivity | Dev Speed | Polish | Ecosystem | **Overall** |
|-----------|--------|--------|------------|-----------|--------|-----------|-------------|
| **Dash** (current) | 9/10 | 7/10 | 8/10 | 6/10 | 7/10 | 9/10 | **8/10** |
| **Panel** (HoloViz) | 8/10 | 9/10 | 8/10 | 7/10 | 8/10 | 6/10 | **7.5/10** |
| **Shiny for Python** | 8/10 | 7/10 | 9/10 | 7/10 | 7/10 | 5/10 | **7/10** |
| **Streamlit** | 8/10 | 6/10 | 5/10 | 9/10 | 8/10 | 9/10 | **6.5/10** |
| **NiceGUI** | 7/10 | 7/10 | 6/10 | 8/10 | 9/10 | 4/10 | **5.5/10** |
| Voila | 7/10 | 6/10 | 4/10 | 5/10 | 4/10 | 5/10 | 3/10 |
| Gradio | 4/10 | 3/10 | 3/10 | 8/10 | 6/10 | 7/10 | 2/10 |
| Reflex | 6/10 | 4/10 | 7/10 | 4/10 | 8/10 | 4/10 | 2.5/10 |

### Recommendation

**Stay with Dash.** The migration cost doesn't justify marginal gains.

**Why:**
- The entire dashboard (7 tabs, complex table-to-chart interactivity, Plotly financial formatting) is already built on Dash
- Plotly charts are best-in-class for financial time series: `hover_data={'Value': ':$,.2f'}`, unified hover, line dash by category
- The pain points (verbose callbacks, DataTable quirks, global state) are all solvable within Dash
- Every alternative requires a full rewrite of 8+ files

**If starting from scratch**, Panel would be the strongest alternative:
- Tabulator table widget is the best data table in any Python framework (sorting, filtering, row selection, cell formatting, conditional formatting — all out of the box)
- hvPlot's `df.hvplot.line()` is more concise than Plotly Express
- Built-in dashboard templates (FastListTemplate, MaterialTemplate) give professional layouts with minimal effort

### Improvements Within Dash

These can be made incrementally without a framework migration:

1. ~~**Replace `dash_table.DataTable` with `dash-ag-grid`**~~ — Done (`fabc329`). All DataTables replaced with AgGrid across all 7 tabs. Row selection, sorting, and filtering now handled natively by ag-grid.

2. ~~**Add `dash-mantine-components`**~~ — Done. Replaced all `dash-bootstrap-components` (`dbc`) with `dash-mantine-components` (`dmc`) across all tab files and the main dashboard. `dbc.Container` → `dmc.Container`, `dbc.Row` → `dmc.Grid`, `dbc.Col` → `dmc.GridCol`, `dbc.Card` → `dmc.Paper`, `dbc.Tabs` → `dmc.Tabs`/`TabsList`/`TabsTab`/`TabsPanel`, wrapped in `dmc.MantineProvider`.

3. ~~**Use Dash Pages / Lazy Loading**~~ — Done (without Dash Pages). Implemented lazy `@property` handlers in `DashboardHandler` plus tab-gated callbacks. Dimension and hypothetical tabs only load data on first visit, achieving the same startup improvement as Dash Pages without the framework restructuring.

4. ~~**Replace `global` state with `dcc.Store`**~~ — Resolved by ag-grid migration (`fabc329`). Global state was only needed to work around DataTable's unreliable `selected_row_ids`. ag-grid's `selectedRows` returns actual row data, so no client-side state storage is needed.

5. ~~**Add `clientside_callback`**~~ — Done. Replaced the `set_asset_dropdown_values` server callback in `hypotheticals_tab.py` with a `clientside_callback` — a 3-line JavaScript function that auto-selects assets when sectors are chosen, with no server round-trip.
