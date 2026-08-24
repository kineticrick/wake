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
