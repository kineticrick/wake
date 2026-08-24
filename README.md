# Wake

*Learn from where you've been.*

A personal investment tracking and analysis platform built with Python, Dash, and MySQL. Imports transaction history from multiple brokerages, computes historical performance across multiple dimensions, and presents everything in an interactive 8-tab web dashboard — including a natural-language chat assistant that answers questions about your portfolio and draws charts on the fly.

---

## Features

### 8-Tab Dashboard

| Tab | Description |
|-----|-------------|
| **Portfolio** | Total portfolio value over time, performance milestones, top winners and losers |
| **Sectors** | Historical return aggregated by sector (Technology, Healthcare, Real Estate, etc.) |
| **Asset Types** | Historical return aggregated by asset type (Common Stock, ETF, REIT) |
| **Account Types** | Historical return aggregated by account type (Discretionary, Retirement) |
| **Geography** | Historical return aggregated by geography (US, ex-US, Global) |
| **Assets** | Per-asset table with multi-filter dropdowns and an interactive price history chart |
| **Hypotheticals** | "What if I'd held?" — price projection for every sold position from exit date to today |
| **Chat** | Ask questions about your portfolio in plain English and get answers + on-the-fly charts |

### Portfolio Tab
- Interactive line chart of total portfolio value with a range slider
- Interval selector (1d / 1w / 1m / 3m / 6m / 1y / 2y / 3y / 5y / Lifetime)
- Dollar-value indicator with delta vs. selected interval start
- Milestone table: value and % return at every standard interval
- **Winners** and **Losers** tables — top/bottom 5 assets by price return for the selected period

### Assets Tab
- Full per-asset table (symbol, name, sector, asset type, account type, geography, quantity, cost basis, current price, current value, milestone returns)
- Multi-select filter dropdowns for Sector, Asset Type, Account Type, and Geography
- Checkbox row selection — pick any subset of assets and see their price history on one chart
- Sortable, filterable columns via ag-grid

### Hypotheticals Tab
- Sector dropdown cascades into an asset dropdown (clientside callback — no server round-trip)
- Normalized % change chart: solid line = actual ownership period, dashed = hypothetical continued hold
- Stats table per exited position: return from entry, return from exit, peak hypothetical return

### Dimension Tabs (Sectors / Asset Types / Account Types / Geography)
- Aggregated summary table: cost basis, current value, % of portfolio, lifetime return, average daily return
- Checkbox selection to overlay specific dimension values on the history chart
- Interval dropdown to zoom the chart to a recent window

### Chat — Ask Your Portfolio
A natural-language assistant embedded as its own tab. Ask things like *"What are the top 5 performing assets in my discretionary account over the last 6 months?"* or *"Show me my portfolio history over 1y"* and get a written answer plus, where useful, a chart rendered inline in the conversation.

- **Tool-calling over the existing analytics**, not text-to-SQL: every number the assistant reports is computed by the same `DashboardHandler` methods and return math (`libraries/returns.py`) that drive the rest of the dashboard, so chat answers always match the charts.
- **Dynamic charts**: ranking and history questions render real plotly figures in the chat thread.
- **Read-only and safe**: the assistant can only call a fixed set of whitelisted data/chart tools — it never writes to the database or runs arbitrary queries.
- **Powered by the Claude API** (`claude-sonnet-4-6` by default), behind a thin provider interface so a local model can be swapped in later.
- Works in demo mode too (answers over synthetic data). Requires an `ANTHROPIC_API_KEY` — see [Enabling Chat](#enabling-chat). Without a key, the tab shows a setup message instead of erroring.

### Demo Mode
Run the full dashboard with zero database or yfinance calls using synthetic GBM-simulated data. Useful for showcasing the app or developing without production data.

---

## Quick Start

### Prerequisites
- Python 3.12+ (required by the pinned `numpy`/`pandas` versions; verified on 3.14)
- MySQL 8.0+
- A virtual environment (recommended)

### Installation

```bash
git clone <repo-url>
cd wake
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Configure your database connection in `libraries/db/dbcfg.py`:

```python
host     = "localhost"
user     = "your_user"
password = "your_password"
database = "portfolio"
port     = 3306
```

### Run in Demo Mode (no database required)

```bash
source venv/bin/activate
python visualization/dash/portfolio_dashboard/portfolio_dashboard.py --demo
```

Open [http://localhost:8050](http://localhost:8050). An orange banner confirms demo mode is active. All data is synthetic — no real financial information is shown.

### Enabling Chat

The **Chat** tab calls the [Claude API](https://www.anthropic.com/api), so it needs an API key. Provide it either way:

**Option A — a `.env` file (recommended):** copy the template and fill in your key.

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
python visualization/dash/portfolio_dashboard/portfolio_dashboard.py        # or --demo
```

The `.env` file is loaded automatically (via `python-dotenv`) and is gitignored, so the key is never committed.

**Option B — an environment variable:** export it in the shell before launching.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python visualization/dash/portfolio_dashboard/portfolio_dashboard.py        # or --demo
```

A key exported in the shell takes precedence over the `.env` file.

- The default model is `claude-sonnet-4-6`. To use the cheaper `claude-haiku-4-5-20251001`, edit `MODEL` in `libraries/chat/config.py`.
- Your portfolio data (tickers, values, returns) is sent to the Claude API per query to answer questions. On standard API terms it is not used for training.
- If `ANTHROPIC_API_KEY` is unset, the rest of the dashboard works normally and the Chat tab simply shows a setup message.

### Run with Real Data

```bash
source venv/bin/activate

# 1. Import brokerage CSVs into MySQL
python generators/importer.py

# 2. (Optional) Regenerate the summary table from position snapshots
python generators/summary_table_generator.py

# 3. Start the dashboard
python visualization/dash/portfolio_dashboard/portfolio_dashboard.py
```

---

## Data Import

### Supported Brokerages
- **Charles Schwab** — place CSVs in `files/transactions/schwab/`
- **TD Ameritrade** — place CSVs in `files/transactions/tdameritrade/`
- **Wallmine** — place CSVs in `files/transactions/wallmine/`

### Other Input Files

| Directory | Contents |
|-----------|----------|
| `files/entities/` | Company metadata: symbol, name, sector, asset type, geography |
| `files/splits/` | Stock split events: symbol, date, multiplier |
| `files/acquisitions/` | M&A events: target symbol, acquirer symbol, conversion ratio, date |
| `files/position_summaries/` | Current holdings snapshots (used by `summary_table_generator.py`) |

### Import Performance

The default `importer.py` is highly optimized vs. the legacy `importer_unoptimized.py`:

| Technique | Benefit |
|-----------|---------|
| `executemany()` bulk INSERTs | 10-100× faster than row-by-row |
| Single reused DB connection | Eliminates per-query connection overhead |
| `ThreadPoolExecutor` across brokerages | Parallel CSV processing |
| Parameterized queries | SQL-injection safe |

### Rebuilding asset history (one-time, after the per-account migration)

`assets_history` stores one row per `(date, symbol, account_type)`. After
pulling the change that introduced this schema, run the one-time rebuild once
before starting the dashboard (the schema changed, so the old table must be
regenerated):

```bash
source venv/bin/activate
python generators/rebuild_asset_history.py
```

This regenerates `assets_history`, `portfolio_history`, and
`assets_hypothetical_history` from your transactions (prices are cached). It is
safe to re-run.

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

**Note:** "no network calls" here means no *market-data* calls (yfinance).
The Chat tab makes a separate, deliberate call to the Anthropic API
(`libraries/chat/provider.py`) on every message send — that's by design and
unaffected by `PORTFOLIO_READ_ONLY`.

---

## Architecture

```
Brokerage CSVs
      │
      ▼
 importer.py  ──────────────────────► MySQL DB
 (parallel,                          (trades, dividends, splits,
  bulk insert)                        acquisitions, entities, summary)
                                           │
                                           ▼
                                    HistoryHandlers
                                    (compute daily value
                                     per asset/dimension,
                                     cache in history tables)
                                           │
                                           ▼
                                    DashboardHandler
                                    (milestones, summaries,
                                     ranked assets)
                                           │
                                           ▼
                                    Dash Web App
                                    (8 interactive tabs)
```

### HistoryHandlers (`libraries/HistoryHandlers/`)

Each handler inherits `BaseHistoryHandler`, which automatically checks whether the DB history is up to date and calls `set_history()` to fill any gaps — unless `PORTFOLIO_READ_ONLY=1` is set, in which case handlers only call `get_history()` and never fetch from yfinance or write to the DB. Keeping history current in that mode is the job of the scheduled jobs described in [Scheduled Updates](#scheduled-updates) above.

| Handler | Output Table | What It Computes |
|---------|-------------|-----------------|
| `AssetHistoryHandler` | `assets_history` | Daily quantity, cost basis, closing price, value, % return per ticker |
| `PortfolioHistoryHandler` | `portfolio_history` | Daily total portfolio value (sum across all assets) |
| `AssetHypotheticalHistoryHandler` | `assets_hypothetical_history` | Hypothetical price continuation for every exited position |
| `SectorHistoryHandler` | `sectors_history` | Average daily % return grouped by sector |
| `AssetTypeHistoryHandler` | `asset_types_history` | Average daily % return grouped by asset type |
| `AccountTypeHistoryHandler` | `account_types_history` | Average daily % return grouped by account type |
| `GeographyHistoryHandler` | `geography_history` | Average daily % return grouped by geography |

Dimension handlers (Sectors, Asset Types, Account Types, Geography) and the Hypothetical handler are **lazy-loaded** — they only compute on the first visit to their respective tab.

### DashboardHandler (`visualization/dash/DashboardHandler.py`)

Central coordinator that:
- Instantiates all HistoryHandlers on startup
- Exposes `portfolio_milestones`, `asset_milestones`, `assets_summary_df`, etc. as properties
- Provides `get_ranked_assets()`, `expand_history_df()`, `gen_historical_stats()`, `_gen_summary_df()`

### Acquisition Handling

Acquisitions are stored as **bidirectional events** in `master_log`:
- `acquisition-target` — the acquired company (quantity goes to 0)
- `acquisition-acquirer` — the acquiring company (receives converted shares at the conversion ratio)

This lets the system track the full investment journey through mergers and acquisitions from both perspectives.

---

## Demo Mode Details

`DemoDashboardHandler` is a `DashboardHandler` subclass that:
- Generates 5+ years of business-day price histories using **Geometric Brownian Motion** (seed = 42, fully reproducible)
- Simulates 12 current holdings (AAPL, MSFT, GOOGL, NVDA, JPM, JNJ, AMZN, SPY, VXUS, VEA, O, AMT) and 4 exited positions (META, TSLA, DIS, PYPL)
- Uses per-asset drift and volatility parameters to produce a realistic mix of winners and losers
- Pre-populates `helpers._entities_df_cache` so all inherited analytics methods work with zero DB calls
- Overrides only `__init__`, `_load_hypotheticals`, and `_load_dimension` — all pure-pandas methods are inherited unchanged

---

## Configuration

### `libraries/globals.py`

| Constant | Default | Purpose |
|----------|---------|---------|
| `MYSQL_CACHE_ENABLED` | `True` | Enable diskcache for DB query results |
| `MYSQL_CACHE_TTL` | `14400` (4 h) | Cache time-to-live in seconds |
| `PORTFOLIO_READ_ONLY` | `False` | Set via `PORTFOLIO_READ_ONLY=1` env var. When true, history handlers only read from the DB — no yfinance calls, no writes. See [Running the dashboard read-only](#running-the-dashboard-read-only) |
| `SYMBOL_BLACKLIST` | `[...]` | Delisted tickers to skip during history updates |
| `ACCOUNT_TYPES` | `['Discretionary', 'Retirement']` | Valid account type labels |
| `FILEDIRS` | `{...}` | Paths to each category of input CSV files |
| `ROOT_DIR` | — | Repository root — update this for deployment |

### Caching Layers

| Layer | Scope | TTL |
|-------|-------|-----|
| diskcache (DB queries) | History table reads | 4 hours |
| `_entities_df_cache` (module-level) | Entities table | Session |
| `_aggregation_cache` (module-level) | Aggregated dimension data | Session |
| Dimension tab `_cache` (closure) | Per-tab summary + column defs | Session |
| yfinance historical prices | Stock price history | 24 hours |
| yfinance current prices | Live prices | 15 minutes |

Cache is invalidated automatically after any `set_history()` write via `mysql_cache_evict()`.

---

## Testing

```bash
source venv/bin/activate

# Run all tests (-t . keeps the repo root as the import root; without it
# discovery treats tests/ as the root and tests.libraries shadows libraries/)
python -m unittest discover -s tests -t . -p "test_*.py" -v

# Run a specific test file
python -m unittest tests.libraries.test_helpers -v

# Run a specific test case
python -m unittest tests.libraries.test_helpers.TestHelpers.test_gen_hist_quantities_basic -v
```

Tests use real database connections and validate core business logic in `libraries/helpers.py` — quantity/cost-basis computation, master log building, and historical value generation.

The chat layer has its own fully offline suite (no database, no network, no API key) under `tests/libraries/chat/`, using a deterministic fake handler and a scripted LLM provider:

```bash
python -m unittest discover -s tests/libraries/chat -p "test_*.py" -v
```

The suite needs nothing beyond `requirements.txt`. If you prefer pytest's
output, install the optional dev extras and use it as a drop-in runner:

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

---

## Project Structure

```
wake/
├── generators/
│   ├── importer.py                    # CSV → MySQL (optimized, parallel)
│   ├── importer_unoptimized.py        # Legacy serial importer (reference only)
│   ├── generator_helpers.py           # Brokerage-specific CSV parsing
│   └── summary_table_generator.py     # Materialize current holdings snapshot
│
├── libraries/
│   ├── globals.py                     # All configuration constants
│   ├── helpers.py                     # Core business logic
│   ├── returns.py                     # Value-weighted / rebased return math
│   ├── chat/                          # Natural-language chat layer
│   │   ├── config.py                  # Model, limits, system prompt
│   │   ├── provider.py                # LLMProvider + AnthropicProvider
│   │   ├── tools.py                   # Tool schemas + dispatcher
│   │   ├── chart_builders.py          # Pure plotly figure builders
│   │   └── engine.py                  # Tool-calling loop
│   ├── HistoryHandlers/
│   │   ├── BaseHistoryHandler.py
│   │   ├── AssetHistoryHandler.py
│   │   ├── PortfolioHistoryHandler.py
│   │   ├── AssetHypotheticalHistoryHandler.py
│   │   ├── SectorHistoryHandler.py
│   │   ├── AssetTypeHistoryHandler.py
│   │   ├── AccountTypeHistoryHandler.py
│   │   └── GeographyHistoryHandler.py
│   ├── db/
│   │   ├── dbcfg.py                   # MySQL connection settings
│   │   ├── MysqlDB.py                 # Context-manager DB wrapper
│   │   ├── sql.py                     # Schema definitions + all queries
│   │   └── mysql_helpers.py           # diskcache utilities
│   ├── yfinance_helpers/              # yfinance wrapper with caching
│   └── pandas_helpers/                # DataFrame utilities (mysql_to_df, etc.)
│
├── visualization/dash/
│   ├── DashboardHandler.py            # Main orchestrator
│   ├── DemoDashboardHandler.py        # Synthetic data for demo mode
│   └── portfolio_dashboard/
│       ├── portfolio_dashboard.py     # Entry point, app layout
│       ├── globals.py                 # DASH_HANDLER instantiation
│       └── tabs/
│           ├── portfolio_tab.py
│           ├── assets_tab.py
│           ├── hypotheticals_tab.py
│           ├── sectors_tab.py
│           ├── asset_types_tab.py
│           ├── account_types_tab.py
│           ├── geography_tab.py
│           ├── chat_tab.py
│           └── dimension_tab_factory.py   # Factory for the 4 dimension tabs
│
├── files/
│   ├── entities/
│   ├── splits/
│   ├── acquisitions/
│   ├── transactions/{schwab,tdameritrade,wallmine}/
│   └── position_summaries/
│
├── tests/
│   └── libraries/
│       └── test_helpers.py
│
├── cache/                             # diskcache directory (auto-created)
└── requirements.txt
```

---

## Extending the Dashboard

### Adding a New Dimension

1. Add the column to the `entities` table in `libraries/db/sql.py`
2. Create a new `HistoryHandler` in `libraries/HistoryHandlers/`
3. Instantiate the handler in `DashboardHandler.__init__()`
4. Call `_gen_summary_df(dimension='NewDim', history_df=...)` for the summary
5. Add a tab file in `visualization/dash/portfolio_dashboard/tabs/` — two lines using the factory
6. Import and wire the tab into `portfolio_dashboard.py`

### Adding a New Brokerage

1. Add a path to `FILEDIRS` in `libraries/globals.py`
2. Add CSV parsing logic to `generator_helpers.py`
3. Call `process_csvs()` in `importer.py` with the new `brokerage_name`
4. Update `validate_transactions()` if the format requires new edge-case handling

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `dash` | Web framework |
| `plotly` | Interactive charts |
| `dash-ag-grid` | High-performance data tables |
| `dash-mantine-components` | UI component library |
| `pandas` | Data manipulation |
| `numpy` | Numerical computation |
| `mysql-connector-python` | MySQL client |
| `yfinance` | Historical and current stock prices |
| `diskcache` | Persistent query caching |
| `anthropic` | Claude API client (powers the Chat tab) |

See `requirements.txt` for pinned versions.
