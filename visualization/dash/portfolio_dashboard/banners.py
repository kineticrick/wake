#!/usr/bin/env python
"""
Dashboard banners.

Kept separate from portfolio_dashboard.py so it can be imported (and tested)
without instantiating DASH_HANDLER, registering Dash callbacks, or touching
the database. This module must stay free of side effects: import only what
build_staleness_banner() needs.
"""

import dash_mantine_components as dmc


def build_staleness_banner(data_as_of, is_stale, price_fetched_at=None,
                           is_price_stale=False):
    """
    Yellow banner naming the data's as-of date when the updater is behind,
    and/or the price-snapshot age when that timer is behind.

    price_fetched_at/is_price_stale are optional (default: not stale) so
    existing history-only callers keep working unchanged.

    Returns None when everything is fresh, so the caller can drop the banner
    from the layout entirely.
    """
    lines = []

    if is_stale:
        if data_as_of is None:
            lines.append("No portfolio history found. Run "
                         "`python generators/daily_update.py` to populate it.")
        else:
            lines.append(f"Data as of {data_as_of}. The daily updater has "
                         f"not run since then — run "
                         f"`python generators/daily_update.py` to refresh.")

    if is_price_stale:
        if price_fetched_at is None:
            lines.append("No current-price snapshot found. Run "
                         "`python generators/price_snapshot.py` to populate "
                         "it.")
        else:
            lines.append(f"Current prices as of {price_fetched_at}. The "
                         f"price-snapshot job has not run since then — run "
                         f"`python generators/price_snapshot.py` to refresh.")

    if not lines:
        return None

    return dmc.Alert(" ".join(lines), color="yellow", variant="filled",
                     mb="xs")
