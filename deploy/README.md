# Deployment

## Setting up on a new machine

A `git clone` gives you code and nothing else. Five things are gitignored and
have never been committed — `files/` (all CSV inputs), `libraries/db/pwd.py`
(MySQL password), `.env` (Anthropic API key), `venv/`, and `cache/` — and the
MySQL database itself obviously does not travel with the repo either.

Work through this in order. Steps 1–4 are required for anything to run at all.

### 1. Virtualenv

```bash
python3 -m venv venv                      # Python 3.12+ (developed on 3.14)
venv/bin/pip install -r requirements.txt
venv/bin/pip install -r requirements-dev.txt   # optional: pytest as a runner
```

### 2. MySQL credentials

```bash
cp libraries/db/pwd.py.example libraries/db/pwd.py
$EDITOR libraries/db/pwd.py                # set mysql_pwd
```

Without this, *every* import of `libraries.db` fails. `dbcfg.py` raises an
`ImportError` naming this step, so the failure explains itself.

User, host, and database name default to `boone` / `127.0.0.1` / `portfolio`
and can be overridden with `WAKE_DB_USER`, `WAKE_DB_HOST`, `WAKE_DB_NAME`
instead of editing `dbcfg.py`.

### 3. The database

Create an empty database matching the name above:

```sql
CREATE DATABASE portfolio;
```

Tables are created on demand — every history handler runs its own
`CREATE TABLE IF NOT EXISTS` in write mode, and `generators/importer.py`
creates the transaction tables. You do not need a schema dump.

### 4. Input data

Copy your `files/` tree across from the old machine (it is gitignored, so
transfer it directly — it holds your actual transaction history):

```
files/
├── entities/            # symbol metadata: sector, asset type, geography
├── splits/
├── acquisitions/
├── transactions/{schwab,tdameritrade,wallmine}/
└── position_summaries/  # current-holdings snapshots
```

Then populate the database:

```bash
venv/bin/python generators/importer.py
venv/bin/python generators/summary_table_generator.py \
    files/position_summaries/<latest>.csv --write-db
venv/bin/python generators/daily_update.py --verbose    # ~90s cold
```

Run them in that order: `daily_update.py` derives history from the imported
transactions, and `price_snapshot.py` takes its symbol list from `summary`.

### 5. Anthropic API key (only if you want the Chat tab)

```bash
cp .env.example .env
$EDITOR .env                               # set ANTHROPIC_API_KEY
```

Everything except the Chat tab works without this.

### 6. Scheduled jobs

```bash
./deploy/install-timers.sh
sudo loginctl enable-linger "$USER"        # needs root; see "Linger" below
```

### 7. Verify

```bash
venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"   # 153 tests, ~10s
PORTFOLIO_READ_ONLY=1 venv/bin/python \
    visualization/dash/portfolio_dashboard/portfolio_dashboard.py
```

### Paths are derived, not configured

`ROOT_DIR` (`libraries/globals.py`) is derived from the source file's own
location, and the systemd units are templates filled in at install time — so a
clone works wherever it lands, with no paths to edit. Two escape hatches exist
for deployments that split code and data: `WAKE_ROOT` overrides the repo root,
and `PORTFOLIO_CACHE_DIR` overrides the diskcache location.

## Scheduled jobs

Wake splits into a **write tier** (scheduled jobs that fetch prices and compute
history) and a **read-only web tier** (the Dash app). The web tier makes no
outbound *market-data* network calls (yfinance) and no database writes.

**Exception:** the Chat tab deliberately calls the Anthropic API
(`libraries/chat/provider.py`) on every message send. That call is
intentional and declared — it's unrelated to the yfinance/DB-write guarantee
above, which is what makes the read-only MySQL grant and public hosting
tractable.

Install the user timers:

```bash
./deploy/install-timers.sh
```

The files in `deploy/systemd/` are **templates** containing `@WAKE_ROOT@` and
`@WAKE_PYTHON@` — do not copy them into `~/.config/systemd/user/` by hand.
The script fills in this checkout's paths, installs the result, runs
`systemd-analyze verify` on each unit, and enables both timers. Pass
`--no-enable` to install without starting them.

It refuses to install if the virtualenv or `libraries/db/pwd.py` is missing,
so a broken setup fails now rather than at 17:30 with nobody watching.

### Linger

```bash
sudo loginctl enable-linger "$USER"
```

User timers stop when you log out. Without linger, the "daily" update only
runs on days you happen to be logged in — and since the dashboard shows a
staleness banner rather than failing, you would see old numbers rather than
an error. Check it with `loginctl show-user "$USER" --property=Linger`.

### Persistent catch-up has one wrinkle

`wake-daily-update.timer` sets `Persistent=true`, so a run missed while the
machine was asleep fires on next boot instead of being skipped. But systemd
only catches up when it has a timestamp from a *previous* trigger — the very
first enable has no stamp, so it will not back-fill a run that was missed
before installation. Bring the data current once by hand after setup:

```bash
venv/bin/python generators/daily_update.py --verbose
```

`wake-price-snapshot.timer` sets `Persistent=false` deliberately: a missed
intraday snapshot is worthless by the time it would be replayed, and catching
up would just fire a burst of stale fetches on wake.

### Market holidays

Neither timer knows about market holidays — both are plain calendar
schedules. On Thanksgiving the updater still runs, finds no new trading day,
and writes nothing. Harmless, just a wasted fetch.

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

### The `summary` table is NOT refreshed by `daily_update.py`

`daily_update.py` brings every `*_history` table up to date, but it deliberately does
not run `generators/summary_table_generator.py` — that script requires a position
snapshot CSV file as a positional argument and a mutually-exclusive action flag, so
it isn't runnable unattended. The `summary` table (current holdings snapshot) is
refreshed only when you manually run the summary generator with a position snapshot:

```bash
python generators/summary_table_generator.py <position_summary_csv> --write-db
```

where `<position_summary_csv>` is a CSV file from your brokerage containing current
holdings (typically placed in `files/position_summaries/`).

**Consequence:** `generators/price_snapshot.py` derives the list of symbols it fetches
from `summary` (via `get_portfolio_summary()`). If you buy a new symbol, it will not
appear in `summary` until `summary_table_generator.py` is re-run with an updated
position snapshot — and so gets no `current_prices` snapshot row, and no current-value
figure in the dashboard, until then. Run the command above with an updated position
summary after any new purchase to pick it up.

## Running the web tier read-only

```bash
PORTFOLIO_READ_ONLY=1 python visualization/dash/portfolio_dashboard/portfolio_dashboard.py
```

If the updater has not run, the dashboard serves the last good data and shows a
banner naming the as-of date. It never blocks to fetch prices itself. In read-only mode,
the Chat tab cannot answer filtered dimension-breakdown questions; unfiltered questions
and all other functionality work normally.

## Moving to another machine

Everything above under "Setting up on a new machine" applies. There are no
paths to edit: `ROOT_DIR` derives from the source location and the unit files
are generated by `deploy/install-timers.sh` for whatever checkout you run it
from. What you must carry across by hand is the gitignored material —
`files/`, `libraries/db/pwd.py`, `.env` — plus the MySQL database, which is
rebuilt from `files/` by the importer rather than transferred.

The `cache/` directory should NOT be copied. It is a derived diskcache,
rebuilt on demand, and it is created `0700` deliberately: diskcache
deserializes with pickle (CVE-2025-69872, no patched release exists), so
anyone able to write into it can execute code in the process that reads it.
Copying it around defeats that.
