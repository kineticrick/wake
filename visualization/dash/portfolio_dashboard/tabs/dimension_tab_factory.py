from dash import callback, dcc, Input, Output, html, no_update
import dash_ag_grid as dag
import plotly.express as px
import pandas as pd
from pandas.tseries.offsets import DateOffset

from libraries.returns import value_weighted_lifetime_return, rebase_to_window_start
from libraries.downsample import downsample_history
from visualization.dash.portfolio_dashboard.globals import *
import dash_mantine_components as dmc


def create_dimension_tab(dimension_name, column_name, summary_df_attr, history_df_attr, tab_id):
    """Factory function to create a dimension tab (sectors, asset_types, etc.)

    Args:
        dimension_name: Tab identifier used in component IDs (e.g. "sectors")
        column_name: DataFrame column name for the dimension (e.g. "Sector")
        summary_df_attr: Attribute name on DASH_HANDLER for summary DataFrame
        history_df_attr: Attribute name on DASH_HANDLER for history DataFrame
        tab_id: The tab ID used in the main Tabs component (e.g. "sectors-dash-tab")

    Returns:
        dmc.Container layout for the tab
    """
    # Cache for lazy-loaded data (populated on first tab visit)
    _cache = {}

    def _get_data():
        """Load data on first access and cache it."""
        if 'summary_df' not in _cache:
            summary_df = getattr(DASH_HANDLER, summary_df_attr)
            _cache['summary_df'] = summary_df
            _cache['column_defs'] = [
                {"field": col, "sortable": True, "filter": True,
                 "checkboxSelection": (col == column_name),
                 "headerCheckboxSelection": (col == column_name)}
                for col in summary_df.columns
            ]
            _cache['row_data'] = summary_df.to_dict('records')
        return _cache

    # Register the combined table + graph update callback
    @callback(
        Output(f'{dimension_name}-table', 'columnDefs'),
        Output(f'{dimension_name}-table', 'rowData'),
        Output(f'{dimension_name}-history-graph', 'figure'),
        Input('tabs', 'value'),
        Input(f'{dimension_name}-table', 'selectedRows'),
        Input(f'{dimension_name}-interval-dropdown', 'value'))
    def update_tab(active_tab, selected_rows, interval):
        if active_tab != tab_id:
            return no_update, no_update, no_update

        data = _get_data()
        full_history = getattr(DASH_HANDLER, history_df_attr)
        interval_days = {k: v for (k, v) in DASH_HANDLER.performance_milestones}

        if interval != "Lifetime":
            days = interval_days[interval]
            start_date = (pd.to_datetime('today') - DateOffset(days=days)).date()

        # --- Table: make VW Return reflect the selected interval ------------
        # For a window, the value-weighted return is the dimension's value at
        # the window end vs its value at the window start. For Lifetime it is
        # the value-weighted return on cost basis at the latest date.
        if interval == "Lifetime":
            latest = full_history[full_history['Date'] == full_history['Date'].max()]
            vw = value_weighted_lifetime_return(
                latest['TotalValue'], latest['TotalCostBasis'])
            vw_map = dict(zip(latest[column_name], vw))
        else:
            window = full_history[full_history['Date'] >= start_date].sort_values('Date')
            grp = window.groupby(column_name)['TotalValue']
            vw_map = ((grp.last() / grp.first() - 1) * 100).to_dict()

        row_data = []
        for row in data['row_data']:
            new_row = dict(row)
            v = vw_map.get(row[column_name])
            if v is not None and pd.notna(v):
                new_row['VW Return'] = round(float(v), 2)
            row_data.append(new_row)

        # --- Chart ----------------------------------------------------------
        chart_df = full_history
        # Filter by selected rows
        if selected_rows:
            selected_values = [row[column_name] for row in selected_rows]
            chart_df = chart_df[chart_df[column_name].isin(selected_values)]

        # Derive the displayed series from the stored dollars.
        if interval == "Lifetime":
            chart_df = chart_df.copy()
            chart_df['y'] = value_weighted_lifetime_return(
                chart_df['TotalValue'], chart_df['TotalCostBasis'])
        else:
            chart_df = chart_df[chart_df['Date'] >= start_date].copy()
            chart_df = chart_df.sort_values(['Date'])
            # Rebase each dimension to ITS OWN value at the window start.
            chart_df['y'] = chart_df.groupby(column_name)['TotalValue'].transform(
                rebase_to_window_start)

        # Thin the payload before serialization. The table above is computed
        # from the FULL frame, so only the chart is affected.
        chart_df = downsample_history(chart_df, group_cols=(column_name,))

        fig = px.line(
            chart_df,
            x=chart_df['Date'],
            y=chart_df['y'],
            hover_data={'y': ':.2f%'},
            color=chart_df[column_name],
        )
        fig.update_layout(height=800)
        fig.update_yaxes(ticksuffix="%")

        return data['column_defs'], row_data, fig

    # Build the layout with empty table (populated on first tab visit)
    tab_layout = dmc.Container(
        [
            dmc.Grid([
                dmc.GridCol(
                    dmc.Paper(
                        dag.AgGrid(
                            id=f'{dimension_name}-table',
                            columnDefs=[],
                            rowData=[],
                            defaultColDef={"resizable": True},
                            dashGridOptions={
                                "rowSelection": {"mode": "multiRow"},
                                "animateRows": False,
                            },
                            style={"height": "400px"},
                        ),
                        shadow="sm", p="md",
                    ),
                    span=12,
                ),
            ]),
            dmc.Grid([
                dmc.GridCol(
                    dcc.Dropdown(
                        id=f'{dimension_name}-interval-dropdown',
                        options=INTERVALS,
                        value=DEFAULT_INTERVAL,
                        placeholder='Select interval',
                    ),
                    span=3, offset=1,
                ),
            ]),
            dmc.Grid([
                dmc.GridCol(
                    dmc.Paper(
                        dcc.Graph(
                            id=f'{dimension_name}-history-graph',
                        ),
                        shadow="sm", p="md",
                    ),
                    span=12,
                ),
            ]),
        ],
        fluid=True,
    )

    return tab_layout
