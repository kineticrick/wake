#!/usr/bin/env python
"""
Dashboard banners.

Kept separate from portfolio_dashboard.py so it can be imported (and tested)
without instantiating DASH_HANDLER, registering Dash callbacks, or touching
the database. This module must stay free of side effects: import only what
build_staleness_banner() needs.
"""

import dash_mantine_components as dmc


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
