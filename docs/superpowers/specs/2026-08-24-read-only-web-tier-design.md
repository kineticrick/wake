# Read-only web tier + scheduled updater

**Date:** 2026-08-24
**Status:** Approved (design)

## Problem

The first time a dimension tab is opened after market close each day, the dashboard
stalls for the better part of a minute. Measured on this machine:

| Path | Time |
|---|---|
| `gen_aggregated_historical_value` cold, full history | 72.9 s |
| `gen_aggregated_historical_value` cold, 3-day window | 58.0 s |
| └─ `yfinance.get_historical_prices`, 1 call, 139 tickers | 11.4 s = **91%** of a warm-cache run |
| └─ everything else (pandas + MySQL) | 1.1 s |

The cause is structural, not algorithmic. `BaseHistoryHandler.__init__` checks whether
its table is behind the last business day and, if so, calls `set_history()` — which
fetches prices for every ticker from Yahoo Finance — **synchronously, inside the Dash
callback that is rendering the tab**. The window size barely matters (58 s for three
days vs 73 s for twelve years) because the cost is the network round-trip for all 139
tickers, not the volume of data processed.

Everything else is already fast. Measured against the running server:

| | |
|---|---|
| Sectors tab, warm (server-side) | 0.20 s (0.26 s with the MySQL cache **disabled**) |
| Sectors round-trip, real browser | 416–876 ms |
| Main-thread blocking during render | 0 ms — Plotly already uses WebGL |
| `DashboardHandler()` startup, warm | 2.5 s |

Two earlier claims in `PERFORMANCE_ANALYSIS.md` and its follow-ups do not survive
measurement and should not drive this work:

- The row-wise `apply` in `aggregate_assets_history_by_symbol` was reported as a ~20 s
  hotspot. That figure was cProfile overhead on 457k `Series.__getitem__` calls; the
  real cost is **0.83 s**. Vectorizing is still worth doing (28× → 0.03 s) but it is a
  cleanup, not the fix.
- Client-side rendering was suspected. It is not a factor on desktop: Plotly 6
  auto-selects WebGL above ~1000 points, and a live `PerformanceObserver` recorded
  **zero** long tasks during a tab switch.

Data volume does not justify a framework migration. The largest table is 117k rows and
the server answers dimension queries in 0.2 s. The problem is *when* work runs, not
*what renders it*. The migration proposed in `FRAMEWORK_COMPARISON.md` is out of scope.

### Secondary problem: payload size

Fine over localhost, painful over cellular — and goal 2 is mobile access:

| Tab | Points | Traces | JSON |
|---|---|---|---|
| Hypotheticals | 264,843 | 126 | 6.42 MB |
| Assets | 63,043 | 30 | 1.53 MB |
| Sectors | 40,700 | 28 | 1.00 MB |

### Secondary problem: unbounded cache

`_aggregation_cache` (`libraries/helpers.py:541`) is a module-level dict keyed by
`(symbols, cadence, start_date, account_type)` with no eviction and no size bound. In a
long-running hosted process with varying filter parameters it grows without limit.

## Decision

Split the system into a **write tier** that runs on a schedule and a **read-only web
tier** that serves the dashboard.

The web tier makes **zero outbound market-data network calls and zero database
writes**. It can run under a read-only MySQL grant. This is the property that makes
public hosting (goal 3) tractable, and it is the reason to prefer this over hiding the
latency behind a background callback: the stall is removed rather than concealed, and
the security posture improves as a side effect.

> **Post-implementation correction (final whole-branch review):** the original "zero
> outbound network calls" framing was inaccurate. The Chat tab (a later addition to this
> branch) makes a deliberate, declared call to the Anthropic API
> (`libraries/chat/provider.py`) on every message send — that is by design and
> orthogonal to the guarantee this spec describes, which is specifically about
> market-data (yfinance) calls and database writes. The review also found that
> `get_historical_prices` (the price-fetch path used by the chat layer's
> account-filtered breakdowns) was not gated by `PORTFOLIO_READ_ONLY` at all — fixed by
> gating it directly and having the chat tool dispatcher degrade gracefully instead of
> performing a live multi-minute fetch inside a request.

**Scope: performance and the read/write split only.** Authentication, TLS, secrets
management, and deployment are goal 3 and get their own spec. Responsive layout and
mobile navigation are goal 2 and get their own spec. This spec includes payload
downsampling because it is a data-shaping concern that belongs with the read path, not a
layout concern.

## Architecture

### 1. `generators/daily_update.py` (new)

Single entry point for all derived-data computation. Runs, in dependency order:

1. `AssetHistoryHandler`
2. `PortfolioHistoryHandler`
3. `AssetHypotheticalHistoryHandler`
4. `SectorHistoryHandler`, `AssetTypeHistoryHandler`, `AccountTypeHistoryHandler`,
   `GeographyHistoryHandler`
5. `summary_table_generator`

All in write mode. The job is idempotent — handlers already use `INSERT IGNORE` /
`REPLACE INTO` keyed on date, so re-running is safe and a partial failure can be
recovered by re-running.

> **Post-implementation correction:** step 5 as implemented (`generators/daily_update.py`)
> does NOT actually run `summary_table_generator` — that script requires a positional
> CSV argument and a mutually-exclusive action flag, so it isn't no-arg runnable from an
> unattended scheduled job. This is correct behavior, not a bug: `summary` refreshes only
> on manual `importer.py` runs. See `deploy/README.md` for the operational consequence
> (a newly bought symbol gets no price-snapshot row until the importer is re-run).

Dead tickers (`$EA: possibly delisted`) are logged and skipped, never fatal. Latency
does not matter in a nightly job, so no blacklist enforcement is added; the existing
retry cost is simply moved off the user's critical path.

### 2. `history_meta` table (new)

One row per run, recording run timestamp, per-table max date, status, and error text.
This is the source of truth for staleness — the web tier reads it instead of computing
freshness from seven separate `MAX(date)` queries.

```sql
CREATE TABLE IF NOT EXISTS history_meta (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    run_started   DATETIME NOT NULL,
    run_finished  DATETIME,
    status        ENUM('running','success','failed') NOT NULL,
    tables_json   JSON,          -- {"sectors_history": "2026-08-24", ...}
    error         TEXT
);
```

### 3. `current_prices` table + intraday refresh (new)

`get_portfolio_current_value()` (`libraries/helpers.py:694`) calls yfinance live. To keep
the web tier network-free, a short-cadence job writes a snapshot table:

```sql
CREATE TABLE IF NOT EXISTS current_prices (
    symbol      VARCHAR(16) PRIMARY KEY,
    price       DECIMAL(18,4) NOT NULL,
    fetched_at  DATETIME NOT NULL
);
```

Refreshed every 15 minutes during market hours — matching the existing
`@cache.memoize(expire=60*15)` window, so perceived freshness is unchanged.
`get_current_price()` reads this table when read-only, and fetches as today when not.

### 4. `BaseHistoryHandler` read-only mode

`BaseHistoryHandler.__init__` gains a `read_only` flag defaulting to
`globals.PORTFOLIO_READ_ONLY` (env `PORTFOLIO_READ_ONLY`). When set, `__init__` skips
the staleness check and `set_history()` entirely and performs only `get_history()`.
Write-mode behavior is unchanged, so `daily_update.py` and the existing rebuild scripts
keep working as they do today.

This is the whole fix for the 58–73 s stall: in read-only mode there is no code path
from a Dash callback to yfinance.

### 5. Staleness surfacing

`DashboardHandler` exposes `data_as_of` (date) and `is_stale` (bool, true when the last
successful run is behind the previous business day). `portfolio_dashboard.py` renders a
`dmc.Alert` showing the as-of date when stale, alongside the existing demo-mode banner.

Stale data is served, never blocked on. Manual refresh is `python generators/daily_update.py`
from the CLI; an authenticated in-app refresh button is deferred to goal 3.

### 6. Scheduling

A systemd **user** timer, weekdays after close, plus `Persistent=true` so a missed run
(laptop asleep) catches up on next boot. A second timer drives the 15-minute price
snapshot during market hours. Unit files live in `deploy/systemd/` and are installed by
the user, not by the app.

### 7. Payload downsampling

Applied in the read path, before figure construction:

- Resample to weekly beyond ~1 year of range; keep daily inside it. At chart resolution
  this is visually indistinguishable — a 40k-point WebGL trace has far more points than
  the ~1500 horizontal pixels available.
- Default the Hypotheticals and Assets tabs to a small subset of series, with the rest
  opt-in via the existing selection controls.

Target: **under 500 KB per tab**, from 6.42 MB / 1.53 MB / 1.00 MB today.

### 8. Cleanups

- Vectorize `aggregate_assets_history_by_symbol` (`libraries/helpers.py:534`). The
  replacement was verified to produce a byte-identical frame (`DataFrame.equals` → True)
  at 0.03 s vs 0.83 s.
- Bound `_aggregation_cache` with an LRU of modest size, or remove it — once the updater
  owns aggregation, the web tier no longer calls `gen_aggregated_historical_value` on
  the request path at all, which may make the cache dead code.

## Data flow

```
                 systemd timers
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
  daily_update.py              price_snapshot.py
  (after close, weekdays)      (every 15m, market hours)
        │                              │
        │ writes                       │ writes
        ▼                              ▼
  ┌──────────────────────────────────────────────┐
  │  MySQL: *_history, summary, history_meta,    │
  │         current_prices                        │
  └──────────────────────────────────────────────┘
                       │ reads only
                       ▼
        Dash web tier (PORTFOLIO_READ_ONLY=1)
        no market-data egress · no writes · read-only grant
        (Chat tab is a declared exception: calls api.anthropic.com)
```

## Error handling

| Failure | Behavior |
|---|---|
| yfinance unavailable during nightly run | Job logs, marks `history_meta.status='failed'`, exits non-zero. DB keeps last good data. Dashboard serves it with a staleness banner. |
| Individual ticker delisted | Logged, skipped, run continues and still succeeds. |
| Job never runs (machine asleep) | `Persistent=true` catches up on boot; banner shows the gap meanwhile. |
| `history_meta` empty (first deploy) | Treated as stale; banner instructs running the updater. |
| Price snapshot stale | `current_prices.fetched_at` surfaces age via the same staleness banner as `history_meta` (threshold: `PRICE_SNAPSHOT_STALE_HOURS`, `libraries/globals.py`); portfolio value falls back to the last close for any symbol missing a snapshot row (fixed in the final review pass — this row was aspirational until then). |
| Web tier attempts a write | Fails at the MySQL grant. This is intended — it makes the read-only contract enforced, not merely conventional. |

## Testing

- **Read-only mode makes no market-data network calls.** Monkeypatch `yfinance_helpers`
  with a fake that raises on any call; construct every handler and `DashboardHandler`
  with `PORTFOLIO_READ_ONLY=1`; assert no invocation. `get_historical_prices` itself is
  gated the same way (raises `ReadOnlyModeError` immediately) since it's reachable
  outside the handler seam via the chat layer's account-filtered breakdowns. This is the
  regression test that keeps the stall from returning. (The Chat tab's Anthropic API call
  is out of scope for this guarantee — see the network-calls correction above.)
- **Vectorization equivalence.** Assert the new `aggregate_assets_history_by_symbol`
  equals the current implementation's output on a fixture with multi-account symbols,
  zero cost basis, and exited assets.
- **Staleness computation.** Table-driven over `history_meta` states: fresh, one day
  behind, weekend, holiday gap, empty table, failed run.
- **Updater idempotency.** Run twice against a fixture DB; assert row counts and values
  are unchanged after the second run.
- **Downsampling fidelity.** Assert the resampled series preserves first/last values and
  stays within a small tolerance of the full-resolution series.

Existing tests must keep passing; write mode is unchanged, so
`tests/libraries/test_base_history_handler.py` should need additions, not rewrites.

## Out of scope

- Authentication, TLS, secrets, deployment target — goal 3 spec.
- Responsive layout, mobile navigation, touch interaction — goal 2 spec.
- Framework migration (`FRAMEWORK_COMPARISON.md`) — not justified by the measurements.
- The plaintext MySQL password in `libraries/db/pwd.py` — noted here, fixed in the goal 3
  spec, since it is a secrets-management concern rather than a performance one.
