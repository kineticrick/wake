"""Tool functions + schemas + dispatcher for the chat layer.

Each tool: (handler, **arguments) -> (text, figure_or_None).
dispatch() routes by name and converts exceptions into error strings so the model
can recover or ask the user to clarify.
"""

import pandas as pd

from libraries.chat import chart_builders
from libraries.chat.config import INTERVALS, DIMENSIONS
from libraries.globals import ReadOnlyModeError
from libraries.helpers import compute_dimension_breakdown

# Maps a filter key the model uses to the summary-df column name.
_FILTER_COLS = {
    "sector": "Sector",
    "asset_type": "AssetType",
    "account_type": "AccountType",
    "geography": "Geography",
}


def _filter_symbols(handler, filters):
    """Return the set of symbols whose holdings match ALL given filters."""
    df = handler.current_portfolio_summary_df
    for key, value in filters.items():
        col = _FILTER_COLS.get(key)
        if col is None:
            raise ValueError(
                f"Unknown filter '{key}'. Valid filters: "
                f"{', '.join(_FILTER_COLS)}.")
        df = df[df[col] == value]
    return set(df["Symbol"])


def rank_assets(handler, interval, count=5, metric="price", ascending=False,
                filters=None):
    ranked = handler.get_ranked_assets(interval, price_or_value=metric,
                                       ascending=ascending)
    if filters:
        keep = _filter_symbols(handler, filters)
        ranked = ranked[ranked["Symbol"].isin(keep)]
    ranked = ranked.head(count)
    return_col = "Price % Return" if metric == "price" else "Value % Return"
    cols = ["Symbol", "Interval", "Current Price", return_col]
    cols = [c for c in cols if c in ranked.columns]
    return ranked[cols].to_string(index=False), None


def get_portfolio_summary(handler, interval="Lifetime"):
    ms = handler.get_portfolio_milestones()
    row = ms[ms["Interval"] == interval]
    if row.empty:
        if interval in INTERVALS:
            # Valid interval, but no value sits exactly on its boundary date.
            return (f"No portfolio value falls on the {interval} boundary date "
                    f"(it may be a non-trading day or before the portfolio's "
                    f"start). Try a nearby interval."), None
        return (f"'{interval}' is not a valid interval. Valid intervals: "
                f"{', '.join(INTERVALS)}."), None
    r = row.iloc[0]
    text = (f"Portfolio at {interval}: value ${r['Value']:,.2f}, "
            f"return {r['Value % Return']:.2f}%. "
            f"Current total value ${handler.current_portfolio_value:,.2f}.")
    return text, None


def get_asset_detail(handler, symbol, interval="Lifetime"):
    summary = handler.current_portfolio_summary_df
    rows = summary[summary["Symbol"] == symbol]
    if rows.empty:
        return f"{symbol} is not currently held.", None
    accounts = ", ".join(sorted(rows["AccountType"].unique()))
    price = rows["Current Price"].iloc[0]
    value = rows["Current Value"].sum()
    cost = rows["Cost Basis"].sum()
    lifetime_return = (value - cost) / cost * 100
    lines = [f"{symbol} ({rows['Name'].iloc[0]}) — held in {accounts}.",
             f"Current price ${price:,.2f}, total value ${value:,.2f}.",
             f"Lifetime return {lifetime_return:.2f}%."]
    if interval != "Lifetime":
        ms = handler.get_asset_milestones(symbols=[symbol])
        m = ms[ms["Interval"] == interval]
        if not m.empty:
            lines.append(f"{interval} price return "
                         f"{m['Price % Return'].iloc[0]:.2f}%.")
    return "\n".join(lines), None


_DIMENSION_ATTRS = {
    "Sector": ("sectors_summary_df", "sectors_history_df"),
    "AssetType": ("asset_types_summary_df", "asset_types_history_df"),
    "AccountType": ("account_types_summary_df", "account_types_history_df"),
    "Geography": ("geography_summary_df", "geography_history_df"),
}


def _split_account_and_entity_filters(filters):
    """Separate the transaction-level account_type filter from the entity-level
    filters (sector/asset_type/geography). Returns (account_type_or_None, dict)."""
    account_type = filters.get("account_type")
    entity = {k: v for k, v in filters.items() if k != "account_type"}
    return account_type, entity


def get_dimension_breakdown(handler, dimension, interval="Lifetime", filters=None):
    if not filters:
        # Fast path: unfiltered breakdown straight from cached summary/history.
        summary_attr, history_attr = _DIMENSION_ATTRS[dimension]
        if interval == "Lifetime":
            df = getattr(handler, summary_attr)
            out = df[[dimension, "Current Value", "VW Return"]].copy()
            return out.to_string(index=False), None
        days = {k: v for (k, v) in handler.performance_milestones}[interval]
        start = (pd.to_datetime("today") - pd.DateOffset(days=days)).normalize()
        hist = getattr(handler, history_attr).copy()
        hist["Date"] = pd.to_datetime(hist["Date"])
        window = hist[hist["Date"] >= start].sort_values("Date")
        grp = window.groupby(dimension)["TotalValue"]
        vw = ((grp.last() / grp.first() - 1) * 100).round(2)
        out = vw.reset_index().rename(columns={"TotalValue": "VW Return"})
        return out.to_string(index=False), None

    # Filtered path: recompute from transactions via the handler seam.
    account_type, entity_filters = _split_account_and_entity_filters(filters)
    symbols = None
    if entity_filters:
        symbols = sorted(_filter_symbols(handler, entity_filters))
        if not symbols:
            return "No holdings match those filters.", None

    if interval == "Lifetime":
        start_date = None
    elif interval in INTERVALS:
        days = {k: v for (k, v) in handler.performance_milestones}[interval]
        start_date = (pd.to_datetime("today")
                      - pd.DateOffset(days=days)).normalize()
    else:
        return (f"'{interval}' is not a valid interval. Valid intervals: "
                f"{', '.join(INTERVALS)}."), None

    agg = handler.get_filtered_dimension_history(
        dimension, account_type=account_type, symbols=symbols,
        start_date=start_date)
    if agg.empty:
        return "No holdings match those filters.", None
    out = compute_dimension_breakdown(agg, dimension,
                                      lifetime=(interval == "Lifetime"))
    return out.to_string(index=False), None


def filter_holdings(handler, filters, columns=None):
    keep = _filter_symbols(handler, filters)
    df = handler.current_portfolio_summary_df
    df = df[df["Symbol"].isin(keep)]
    if columns:
        columns = [c for c in columns if c in df.columns]
        df = df[columns]
    else:
        df = df[["Symbol", "Name", "AccountType", "Current Value"]]
    return df.to_string(index=False), None


def show_ranked_bar(handler, interval, count=5, metric="price", ascending=False,
                    filters=None):
    ranked = handler.get_ranked_assets(interval, price_or_value=metric,
                                       ascending=ascending)
    if filters:
        keep = _filter_symbols(handler, filters)
        ranked = ranked[ranked["Symbol"].isin(keep)]
    ranked = ranked.head(count)
    return_col = "Price % Return" if metric == "price" else "Value % Return"
    fig = chart_builders.build_ranked_bar(
        ranked, label_col="Symbol", value_col=return_col,
        title=f"{interval} ranked assets")
    summary = ranked[["Symbol", return_col]].to_string(index=False)
    return summary, fig


def show_history_line(handler, target_type, targets, interval="Lifetime",
                      filters=None):
    if interval == "Lifetime":
        start = pd.Timestamp.min
    else:
        days = {k: v for (k, v) in handler.performance_milestones}[interval]
        start = (pd.to_datetime("today") - pd.DateOffset(days=days)).normalize()

    if target_type == "portfolio":
        df = handler.portfolio_history_df.reset_index()
        df = df.rename(columns={df.columns[0]: "Date"})
        df["Date"] = pd.to_datetime(df["Date"])
        df["Label"] = "Portfolio"
        df = df[df["Date"] >= start]
        fig = chart_builders.build_history_line(
            df, label_col="Label", value_col="Value", title="Portfolio history")
        return f"Portfolio history over {interval}.", fig

    if target_type == "asset":
        df = handler.portfolio_assets_history_expanded_df.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df[df["Symbol"].isin(targets)]
        df = df[df["Date"] >= start]
        fig = chart_builders.build_history_line(
            df, label_col="Symbol", value_col="ClosingPrice",
            title=f"{', '.join(targets)} over {interval}")
        return f"Price history for {', '.join(targets)} over {interval}.", fig

    if target_type == "dimension":
        if not targets:
            return "targets must contain exactly one dimension name.", None
        if targets[0] not in _DIMENSION_ATTRS:
            return (f"Unknown dimension '{targets[0]}'. "
                    f"Valid: {list(_DIMENSION_ATTRS)}."), None

        if filters:
            account_type, entity_filters = \
                _split_account_and_entity_filters(filters)
            symbols = None
            if entity_filters:
                symbols = sorted(_filter_symbols(handler, entity_filters))
                if not symbols:
                    return "No holdings match those filters.", None
            start_date = None if interval == "Lifetime" else start
            agg = handler.get_filtered_dimension_history(
                targets[0], account_type=account_type, symbols=symbols,
                start_date=start_date)
            if agg.empty:
                return "No holdings match those filters.", None
            df = agg.rename(columns={"total_value": "TotalValue"})
        else:
            summary_attr, history_attr = _DIMENSION_ATTRS[targets[0]]
            df = getattr(handler, history_attr).copy()
            df["Date"] = pd.to_datetime(df["Date"])
            df = df[df["Date"] >= start]

        fig = chart_builders.build_history_line(
            df, label_col=targets[0], value_col="TotalValue",
            title=f"{targets[0]} over {interval}")
        return f"{targets[0]} history over {interval}.", fig

    return f"Unknown target_type: {target_type}", None


# ---- dispatcher ---------------------------------------------------------------

_TOOLS = {
    "rank_assets": rank_assets,
    "get_portfolio_summary": get_portfolio_summary,
    "get_asset_detail": get_asset_detail,
    "get_dimension_breakdown": get_dimension_breakdown,
    "filter_holdings": filter_holdings,
    "show_ranked_bar": show_ranked_bar,
    "show_history_line": show_history_line,
}


def dispatch(handler, name, arguments):
    """Run a tool by name. Returns (text, figure_or_None). Never raises."""
    fn = _TOOLS.get(name)
    if fn is None:
        return f"Unknown tool: {name}", None
    try:
        return fn(handler, **arguments)
    except ReadOnlyModeError:
        # get_filtered_dimension_history recomputes from transactions +
        # prices (the stored dimension tables aren't filtered), which
        # needs a live yfinance fetch that read-only mode refuses to make.
        # Fail soft with a message the model can relay, rather than letting
        # the generic handler below turn it into a raw exception string.
        return ("Filtered dimension breakdowns aren't available in "
                "read-only mode; ask about the whole portfolio instead."), None
    except Exception as exc:  # surfaced back to the model as a tool result
        return f"Error running {name}: {exc}", None


TOOL_SCHEMAS = [
    {
        "name": "rank_assets",
        "description": "Rank currently-held assets by return over an interval. "
                       "Returns the top/bottom N as text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "interval": {"type": "string", "enum": INTERVALS},
                "count": {"type": "integer", "default": 5},
                "metric": {"type": "string", "enum": ["price", "value"],
                           "default": "price"},
                "ascending": {"type": "boolean", "default": False},
                "filters": {
                    "type": "object",
                    "description": "Optional dimension filters, e.g. "
                                   "{\"account_type\": \"Retirement\"}.",
                    "properties": {
                        "sector": {"type": "string"},
                        "asset_type": {"type": "string"},
                        "account_type": {"type": "string"},
                        "geography": {"type": "string"},
                    },
                },
            },
            "required": ["interval"],
        },
    },
    {
        "name": "get_portfolio_summary",
        "description": "Total portfolio value and return at an interval.",
        "input_schema": {
            "type": "object",
            "properties": {"interval": {"type": "string", "enum": INTERVALS}},
            "required": [],
        },
    },
    {
        "name": "get_asset_detail",
        "description": "Details for one ticker: price, value, return, accounts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "interval": {"type": "string", "enum": INTERVALS},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_dimension_breakdown",
        "description": "Value-weighted return and value by a dimension over an "
                       "interval. Optionally filter by account_type, sector, "
                       "asset_type, or geography via `filters`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dimension": {"type": "string", "enum": DIMENSIONS},
                "interval": {"type": "string", "enum": INTERVALS},
                "filters": {
                    "type": "object",
                    "description": "Optional filters, e.g. "
                                   "{\"account_type\": \"Discretionary\"}.",
                    "properties": {
                        "sector": {"type": "string"},
                        "asset_type": {"type": "string"},
                        "account_type": {"type": "string"},
                        "geography": {"type": "string"},
                    },
                },
            },
            "required": ["dimension"],
        },
    },
    {
        "name": "filter_holdings",
        "description": "List holdings matching dimension filters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {
                        "sector": {"type": "string"},
                        "asset_type": {"type": "string"},
                        "account_type": {"type": "string"},
                        "geography": {"type": "string"},
                    },
                },
                "columns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["filters"],
        },
    },
    {
        "name": "show_ranked_bar",
        "description": "Render a BAR CHART of top/bottom N assets by return.",
        "input_schema": {
            "type": "object",
            "properties": {
                "interval": {"type": "string", "enum": INTERVALS},
                "count": {"type": "integer", "default": 5},
                "metric": {"type": "string", "enum": ["price", "value"]},
                "ascending": {"type": "boolean", "default": False},
                "filters": {"type": "object"},
            },
            "required": ["interval"],
        },
    },
    {
        "name": "show_history_line",
        "description": "Render a rebased % LINE CHART. target_type is 'portfolio', "
                       "'asset' (targets=list of tickers), or 'dimension' "
                       "(targets=[dimension name]). For 'dimension' you may pass "
                       "`filters` (account_type/sector/asset_type/geography).",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string",
                                "enum": ["portfolio", "asset", "dimension"]},
                "targets": {"type": "array", "items": {"type": "string"}},
                "interval": {"type": "string", "enum": INTERVALS},
                "filters": {
                    "type": "object",
                    "description": "Only for target_type='dimension'.",
                    "properties": {
                        "sector": {"type": "string"},
                        "asset_type": {"type": "string"},
                        "account_type": {"type": "string"},
                        "geography": {"type": "string"},
                    },
                },
            },
            "required": ["target_type", "targets"],
        },
    },
]
