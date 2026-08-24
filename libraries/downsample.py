#!/usr/bin/env python
"""
Chart payload shaping.

Pure functions -- no DB, no network -- so they can be unit tested directly and
called from any Dash callback.
"""
import datetime
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from libraries.globals import DOWNSAMPLE_DAILY_WINDOW_DAYS, DEFAULT_CHART_SERIES


def downsample_history(df: pd.DataFrame, date_col: str = 'Date',
                       group_cols=(), window_days: int = None,
                       today: datetime.date = None) -> pd.DataFrame:
    """
    Keep daily resolution inside `window_days` of today; weekly (Fridays)
    before that. Each group's first and last points are always preserved so
    endpoints and totals still line up.

    group_cols: e.g. ('Symbol',) or ('Sector',). Empty for single-series data.
    today:      injectable for tests.
    """
    if df.empty:
        return df

    if window_days is None:
        window_days = DOWNSAMPLE_DAILY_WINDOW_DAYS
    if today is None:
        today = datetime.date.today()

    group_cols = list(group_cols)
    cutoff = pd.Timestamp(today - datetime.timedelta(days=window_days))
    dates = pd.to_datetime(df[date_col])

    old = df[dates < cutoff]
    if old.empty:
        return df

    recent = df[dates >= cutoff]
    old_dates = pd.to_datetime(old[date_col])

    # Fridays only, beyond the daily window.
    keep = old[old_dates.dt.weekday == 4]

    # Always retain each group's endpoints, otherwise a series can start or end
    # at a different value than the full-resolution data.
    if group_cols:
        first = old.loc[old.groupby(group_cols)[date_col].idxmin()]
        last = old.loc[old.groupby(group_cols)[date_col].idxmax()]
    else:
        first = old.loc[[old[date_col].idxmin()]]
        last = old.loc[[old[date_col].idxmax()]]

    keep = pd.concat([keep, first, last])
    keep = keep[~keep.index.duplicated(keep='first')]

    out = pd.concat([keep, recent])
    out = out.sort_values(group_cols + [date_col])
    return out.reset_index(drop=True)


def top_n_symbols(df: pd.DataFrame, symbol_col: str = 'Symbol',
                  value_col: str = 'ClosingPrice % Change',
                  n: int = None) -> list:
    """
    The n symbols with the largest absolute value in `value_col`.

    Used to pick a default subset for charts that would otherwise ship every
    series (Hypotheticals renders 126 traces / 6.4 MB by default).
    """
    if df.empty:
        return []
    if n is None:
        n = DEFAULT_CHART_SERIES

    extremes = df.groupby(symbol_col)[value_col].apply(
        lambda s: s.abs().max())
    return extremes.sort_values(ascending=False).head(n).index.tolist()
