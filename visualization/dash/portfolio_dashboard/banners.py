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
                           is_price_stale=False, lagging=None):
    """
    Yellow banner naming the data's as-of date when the updater is behind,
    and/or the price-snapshot age when that timer is behind, and/or any table
    that has drifted behind the others.

    price_fetched_at/is_price_stale/lagging are optional (default: nothing
    wrong) so existing callers keep working unchanged.

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

    # A table drifting behind the others is a different failure from the
    # updater being down, and it used to be invisible: as_of was the OLDEST
    # per-table date, so one lagging table silently dragged the headline for
    # all of them. Reported separately, and only when the updater itself is
    # healthy -- if the whole pipeline is behind, saying so once is enough.
    if lagging and not is_stale:
        names = ", ".join(lagging)
        lines.append(f"Note: {names} "
                     f"{'is' if len(lagging) == 1 else 'are'} behind the other "
                     f"history tables. The tab(s) built from "
                     f"{'it' if len(lagging) == 1 else 'them'} show older data "
                     f"than the rest of the dashboard.")

    if not lines:
        return None

    return dmc.Alert(" ".join(lines), color="yellow", variant="filled",
                     mb="xs")
