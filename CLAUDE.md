# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a portfolio analysis system that tracks investment performance across multiple dimensions (assets, sectors, asset types, account types, geography). It imports transaction data from multiple brokerages, stores it in MySQL, and provides a Dash-based dashboard for visualization and analysis.

## Development Commands

### Environment Setup
```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Testing
```bash
# Run all tests (-t . keeps the repo root as the import root; without it,
# discovery treats tests/ as the root and tests.libraries shadows libraries/,
# so every test module fails with ModuleNotFoundError)
python -m unittest discover -s tests -t . -p "test_*.py" -v

# Run specific test file
python -m unittest tests.libraries.test_helpers -v

# Run specific test class or method
python -m unittest tests.libraries.test_helpers.TestHelpers.test_gen_hist_quantities_basic -v
```

`discover -s tests -t . ...` collects the full suite (137 tests as of this
writing), including `tests/libraries/chat/` and `tests/generators/`. All of it
runs offline against mocks/a real MySQL connection in ~10s — no yfinance
network calls are made. Use the specific-file/method forms above when you only
need a fast, targeted run.

### Running the Dashboard
```bash
# Start the Dash web application
python visualization/dash/portfolio_dashboard/portfolio_dashboard.py

# The dashboard will be available at http://localhost:8050
```

### Data Import
```bash
# Import transaction data from CSV files into MySQL database
python generators/importer.py

# Legacy importer (slower, serial execution - for reference only)
python generators/importer_unoptimized.py

# Generate summary tables
python generators/summary_table_generator.py
```

**Note:** The default `importer.py` is optimized and provides 10-100x speedup through:
- Bulk INSERT operations using `executemany()` instead of individual INSERTs
- Single database connection instead of one per query
- Parallel CSV processing across brokerages using ThreadPoolExecutor
- Parameterized queries (prevents SQL injection)

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

## Architecture

### Data Flow
1. **CSV Import** → CSV files in `files/` directories (transactions, entities, splits, acquisitions)
2. **Processing** → `generators/importer.py` validates and cleans data using `generator_helpers.py`
   - Parallel CSV processing for multiple brokerages
   - Bulk database inserts for performance
3. **Storage** → MySQL database with tables: trades, dividends, splits, entities, acquisitions
4. **History Generation** → HistoryHandlers compute historical values by combining transaction data with yfinance price data
5. **Visualization** → DashboardHandler aggregates data and serves it to Dash tabs

### Key Components

#### HistoryHandlers (libraries/HistoryHandlers/)
All handlers inherit from `BaseHistoryHandler` which implements automatic history updates:
- Checks DB for latest history date
- Fetches new data if behind current trading day or if weekend gaps exist
- Uses `get_history()` (retrieve from DB) and `set_history()` (compute and store) pattern
- **Exception:** when `PORTFOLIO_READ_ONLY=1` (see `libraries/globals.py`), handlers only call `get_history()` — no yfinance calls, no DDL, no DB writes. This is the mode the Dash web tier runs in; `generators/daily_update.py` is the scheduled job that writes history instead (see Scheduled Updates above).

**Handler types:**
- `AssetHistoryHandler` - Individual asset (ticker) values over time
- `PortfolioHistoryHandler` - Total portfolio value (aggregates all assets)
- `AssetHypotheticalHistoryHandler` - "What if we held" analysis for sold assets
- `SectorHistoryHandler` - Aggregated values by sector (Technology, Healthcare, etc.)
- `AssetTypeHistoryHandler` - Aggregated values by type (ETF, Common Stock, REIT, etc.)
- `AccountTypeHistoryHandler` - Aggregated values by account type (Discretionary, Retirement)
- `GeographyHistoryHandler` - Aggregated values by geography (US, ex-US, Global)

#### DashboardHandler (visualization/dash/DashboardHandler.py)
Orchestrates all history handlers and computes:
- Current portfolio summaries (value, cost basis, returns)
- Performance milestones (1d, 1w, 1m, 3m, 6m, 1y, 2y, 3y, 5y, lifetime)
- Ranked assets by return
- Historical statistics (sharpe ratio, sortino ratio, etc.)

Provides methods:
- `get_portfolio_milestones()` - Portfolio returns at key intervals
- `get_asset_milestones()` - Individual asset returns at key intervals
- `get_ranked_assets(interval, price_or_value, ascending, count)` - Top/bottom performers
- `expand_history_df()` - Adds % change columns and asset metadata to history
- `gen_historical_stats()` - Computes statistics (returns, sharpe, sortino) for assets

#### Database Layer (libraries/db/)
- `MysqlDB` - Context manager wrapper around mysql.connector
- `mysql_helpers.py` - Query caching utilities (controlled by `MYSQL_CACHE_ENABLED` in globals.py)
- `sql.py` - All SQL schema and query definitions
- `dbcfg.py` - Database connection configuration

#### Helper Libraries
- `libraries/helpers.py` - Core business logic:
  - `build_master_log()` - Merges all transaction events into chronological log
  - `gen_hist_quantities()` - Computes running quantity/cost basis, handles splits/acquisitions
  - `gen_assets_historical_value()` - Combines quantities with historical prices
  - `get_portfolio_current_value()` - Current holdings from summary table + yfinance prices
- `libraries/yfinance_helpers/` - Wrapper around yfinance for price data
- `libraries/pandas_helpers/` - DataFrame utilities including `mysql_to_df()` with caching

### Important Patterns

#### Account Type Dimension
Account types ('Discretionary', 'Retirement') are stored in trades/dividends tables and tracked throughout the system. When adding account type filtering, ensure it's applied at the transaction level in `build_master_log()`.

#### Geography Dimension
Geography ('US', 'ex-US', 'Global') is stored in the entities table and joined with portfolio data. Recent addition as of June 2025.

#### Acquisition Handling
Acquisitions are stored as bidirectional events in master_log:
- `acquisition-target` - The acquired company (quantity goes to 0)
- `acquisition-acquirer` - The acquiring company (receives converted shares)
This allows tracking the acquisition from both perspectives.

#### Caching Strategy
- History queries are cached using diskcache (tag: `MYSQL_CACHE_HISTORY_TAG`)
- Cache is invalidated when new history is written via `mysql_cache_evict()`
- Control caching via `MYSQL_CACHE_ENABLED` in `libraries/globals.py`

## File Structure Notes

### Input Data (files/)
- `entities/` - Company metadata CSVs (symbol, name, sector, asset_type, geography)
- `splits/` - Stock split events
- `acquisitions/` - Merger/acquisition events with conversion ratios
- `transactions/{schwab,tdameritrade,wallmine}/` - Brokerage transaction CSVs
- `position_summaries/` - Current holdings snapshots (used by summary_table_generator.py)

### Configuration
- `libraries/globals.py` - All global constants:
  - File paths (update `ROOT_DIR` for deployment)
  - Cache settings
  - Symbol blacklist (delisted/problematic tickers)
  - Dimension values (ACCOUNT_TYPES, etc.)

### Testing
Tests use real data and database connections. To add tests for new functions in `libraries/helpers.py`, follow the pattern in `test_helpers.py` with setUp() creating sample DataFrames.

## Common Workflows

### Adding a New Dimension
1. Add column to entities table (sql.py)
2. Create new HistoryHandler in `libraries/HistoryHandlers/`
3. Instantiate handler in `DashboardHandler.__init__()`
4. Create summary using `_gen_summary_df(dimension='NewDim', history_df=...)`
5. Add tab in `visualization/dash/portfolio_dashboard/tabs/`
6. Import and add tab to layout in `portfolio_dashboard.py`

### Adding a New Brokerage Import
1. Add directory to `FILEDIRS` in globals.py
2. Add CSV parsing logic in `generator_helpers.py` (handle brokerage-specific formats)
3. Call `process_csvs()` in `importer.py` with `brokerage_name` parameter
4. Update `validate_transactions()` if needed for new edge cases
