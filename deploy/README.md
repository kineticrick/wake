# Deployment

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

**Note:** paths in the unit files are absolute and assume the repo lives at
`/home/kineticrick/code/python/wake`. Update them if you deploy elsewhere.
