from dash import callback, dcc, Input, Output, html
import dash_ag_grid as dag
import plotly.express as px
import plotly.graph_objs as go
import pandas as pd

from visualization.dash.portfolio_dashboard.globals import *
from libraries.downsample import downsample_history
from visualization.dash.assets_chart_helpers import prepare_per_account_chart_df

import dash_mantine_components as dmc

# Get assets table and prepare to be displayed
assets_table_df = DASH_HANDLER.assets_summary_df
assets_table_df = assets_table_df.rename(columns={'Lifetime Return': 'Lifetime'})

# Build column definitions for ag-grid
column_defs = [{"field": col, "sortable": True, "filter": True,
                "checkboxSelection": (col == "Symbol"),
                "headerCheckboxSelection": (col == "Symbol")}
               for col in assets_table_df.columns]

# Retrieve dropdown options for filtering
sectors = sorted([{'label': x, 'value': x}
                  for x in assets_table_df['Sector'].unique()],
                 key=lambda x: x['label'])

asset_types = sorted([{'label': x, 'value': x}
                      for x in assets_table_df['AssetType'].unique()],
                     key=lambda x: x['label'])

account_types = sorted([{'label': x, 'value': x}
                        for x in assets_table_df['AccountType'].unique()],
                       key=lambda x: x['label'])

geography = sorted([{'label': x, 'value': x}
                    for x in assets_table_df['Geography'].unique()],
                   key=lambda x: x['label'])


@callback(
    Output('assets-table', 'rowData'),
    Input('sector-select-dropdown', 'value'),
    Input('asset-type-select-dropdown', 'value'),
    Input('account-type-select-dropdown', 'value'),
    Input('geography-select-dropdown', 'value'),
    Input('assets-interval-dropdown', 'value')
)
def update_assets_table(sectors_sel, asset_types_sel, account_types_sel, geography_sel, interval):
    try:
        df = assets_table_df.copy()

        # Sort by the selected interval column
        df = df.sort_values(by=interval, ascending=False)

        # Filter by dropdown selections (union of all filters)
        if sectors_sel or asset_types_sel or account_types_sel or geography_sel:
            masks = []
            if sectors_sel:
                masks.append(df['Sector'].isin(sectors_sel))
            if asset_types_sel:
                masks.append(df['AssetType'].isin(asset_types_sel))
            if account_types_sel:
                masks.append(df['AccountType'].isin(account_types_sel))
            if geography_sel:
                masks.append(df['Geography'].isin(geography_sel))

            # Union of all filter masks
            combined_mask = masks[0]
            for mask in masks[1:]:
                combined_mask = combined_mask | mask
            df = df[combined_mask]

        return df.to_dict('records')
    except Exception as e:
        print(f"Error in update_assets_table: {e}")
        return []


@callback(
    Output('assets-history-graph', 'figure'),
    Input('assets-table', 'selectedRows'),
    Input('assets-interval-dropdown', 'value'))
def update_assets_hist_graph(selected_rows, interval):
    try:
        expanded = DASH_HANDLER.portfolio_assets_history_by_account_expanded_df
        selected_pairs = (
            {(r['Symbol'], r['AccountType']) for r in selected_rows}
            if selected_rows else None)

        df = prepare_per_account_chart_df(
            expanded, DASH_HANDLER.current_portfolio_summary_df,
            selected_pairs, interval, DASH_HANDLER.performance_milestones)

        if df.empty:
            return go.Figure().update_layout(
                title="No data available. Select assets from the table above.")

        # Thin to the shared point budget. This tab has the most traces (34),
        # so it is the binding constraint on the 500 KB payload target.
        df = downsample_history(df, group_cols=('Symbol', 'AccountType'))

        # color = ticker (a ticker's accounts share a color),
        # dash  = account (distinguishes Discretionary vs Retirement).
        # The rich hover (dollar Value, account label, formatted % change) is
        # only affordable once the user has narrowed the view -- at the full
        # ~34-trace default it roughly doubles the payload. Selecting rows
        # restores it.
        line_kwargs = {}
        if selected_rows:
            line_kwargs['hover_data'] = {
                'Value': ':$,.2f', 'AccountType': True,
                'ClosingPrice % Change': ':.2f%'}
        fig = px.line(
            df,
            x='Date',
            y='ClosingPrice % Change',
            color='Symbol',
            line_dash='AccountType',
            **line_kwargs,
        )
        fig.update_layout(height=800)
        fig.update_yaxes(ticksuffix="%")
        return fig
    except Exception as e:
        print(f"Error in update_assets_hist_graph: {e}")
        return go.Figure().update_layout(title=f"Error loading chart: {str(e)}")

assets_tab = dmc.Container(
    [
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    dag.AgGrid(
                        id='assets-table',
                        columnDefs=column_defs,
                        rowData=assets_table_df.to_dict('records'),
                        defaultColDef={"resizable": True},
                        dashGridOptions={
                            "rowSelection": {"mode": "multiRow"},
                            "animateRows": False,
                            "pagination": True,
                            "paginationPageSize": 50,
                        },
                        style={"height": "600px"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 12},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    id='assets-interval-dropdown',
                    options=INTERVALS,
                    value=DEFAULT_INTERVAL,
                    placeholder='Select interval',
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    options=sectors,
                    id='sector-select-dropdown',
                    placeholder='Select sector(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    options=asset_types,
                    id='asset-type-select-dropdown',
                    placeholder='Select asset type(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    options=account_types,
                    id='account-type-select-dropdown',
                    placeholder='Select account type(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    options=geography,
                    id='geography-select-dropdown',
                    placeholder='Select geography(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    dcc.Graph(
                        id='assets-history-graph',
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 12},
            ),
        ]),
    ],
    fluid=True,
)
