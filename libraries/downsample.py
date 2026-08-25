#!/usr/bin/env python
"""
Chart payload shaping.

Pure functions -- no DB, no network -- so they can be unit tested directly and
called from any Dash callback.
"""
import math
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd

from libraries.globals import CHART_POINT_BUDGET, DEFAULT_CHART_SERIES


def _thin_one_series(group: pd.DataFrame, date_col: str,
                     budget: int) -> pd.DataFrame:
    """
    Thin a single series to at most `budget` rows with an even stride.

    Always keeps the first and last row: chart endpoints must continue to
    agree with the figures in the table beside the chart.
    """
    n = len(group)
    if n <= budget:
        return group

    group = group.sort_values(date_col)
    # ceil so the stride never leaves us above budget: n=1000, budget=300
    # gives stride 4 -> 250 kept, +1 for the final row.
    stride = math.ceil(n / budget)
    kept = group.iloc[::stride]

    last = group.iloc[[-1]]
    if kept.index[-1] != last.index[0]:
        # The strided sample can already land exactly on `budget` points
        # (e.g. n=1095, budget=100 -> stride 11 -> 100 points, last at
        # position 1089 vs true last 1094). Appending unconditionally would
        # then overshoot the budget by one, so drop the tail-most strided
        # point to make room -- the true last row still anchors the end.
        if len(kept) >= budget:
            kept = kept.iloc[:-1]
        kept = pd.concat([kept, last])
    return kept


def downsample_history(df: pd.DataFrame, date_col: str = 'Date',
                       group_cols=(),
                       max_points_per_series: int = None) -> pd.DataFrame:
    """
    Thin each series to at most `max_points_per_series` points.

    A point budget rather than a calendar window: a chart renders into a fixed
    number of pixels, so the useful number of points is bounded by the display,
    not by how much history exists. The previous fixed-window approach grew the
    payload with every year of data.

    Each group's first and last points always survive.

    group_cols: e.g. ('Symbol',) or ('Symbol', 'AccountType') -- whatever
                identifies one chart trace. Empty for single-series data.
    """
    if df.empty:
        return df

    # idxmin()/idxmax()-style label lookups and per-group slicing both misbehave
    # on a non-unique index: a caller concatenating two independently-indexed
    # per-symbol frames can otherwise lose a whole series with no error.
    df = df.reset_index(drop=True)

    if max_points_per_series is None:
        max_points_per_series = CHART_POINT_BUDGET

    # Preserving both endpoints (see _thin_one_series) requires room for at
    # least two points, so clamp a degenerate budget rather than silently
    # dropping the first point.
    max_points_per_series = max(max_points_per_series, 2)

    group_cols = list(group_cols)
    if not group_cols:
        return _thin_one_series(
            df, date_col, max_points_per_series).reset_index(drop=True)

    # Explicit iteration rather than groupby().apply(): apply's handling of
    # grouping columns is a moving target across pandas versions, and this is
    # clearer about what it returns.
    pieces = [_thin_one_series(group, date_col, max_points_per_series)
              for _, group in df.groupby(group_cols, sort=False)]

    out = pd.concat(pieces)
    return out.sort_values(group_cols + [date_col]).reset_index(drop=True)


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
