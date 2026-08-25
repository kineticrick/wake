from dash import callback, dcc, html, Input, Output
import plotly.graph_objs as go
import plotly.express as px
import pandas as pd
from visualization.dash.portfolio_dashboard.globals import *
from pandas.tseries.offsets import DateOffset
from libraries.returns import value_weighted_lifetime_return, rebase_to_window_start
import dash_mantine_components as dmc
from visualization.dash.portfolio_dashboard.components.responsive_table import (
    build_mobile_cards, responsive_table)

# Build column defs for the static milestones table
milestones_column_defs = [{"field": col, "sortable": True, "filter": True}
                          for col in PORTFOLIO_MILESTONES.columns]

# Card fields for the phone view of each table. These names are exact: the
# milestones frame is PORTFOLIO_MILESTONES[['Interval', 'Value',
# 'Value % Return']], and update_asset_tables selects ['Symbol', 'Interval',
# 'Current Price', 'Price', 'Price % Return']. A name that is not in the row
# silently renders an empty card, so do not guess these.
MILESTONE_MOBILE_FIELDS = ['Value', 'Value % Return']
MOVERS_MOBILE_FIELDS = ['Current Price', 'Price % Return']

@callback(
    Output('portfolio-history-graph', 'figure'),
    Input('interval-dropdown', 'value'))
def update_port_hist_graph(interval):
    try:
        interval_days = {k:v for (k,v) in DASH_HANDLER.performance_milestones}

        port_hist_df = DASH_HANDLER.portfolio_history_df

        if interval == "Lifetime":
            date = port_hist_df.index[0]
        else:
            days = interval_days.get(interval, 365)
            offset = DateOffset(days=days)
            date = pd.to_datetime('today') - offset
            date = date.strftime('%Y-%m-%d')

        port_hist_df = port_hist_df[port_hist_df.index >= date].copy()

        if port_hist_df.empty:
            return go.Figure().update_layout(title="No data available for selected interval")

        port_hist_df['Value'] = port_hist_df['Value'].astype(float)
        port_hist_df['CostBasis'] = port_hist_df['CostBasis'].astype(float)

        if interval == "Lifetime":
            # Value-weighted return on invested capital
            port_hist_df['y'] = value_weighted_lifetime_return(
                port_hist_df['Value'], port_hist_df['CostBasis'])
        else:
            port_hist_df['y'] = rebase_to_window_start(port_hist_df['Value'])

        fig = px.line(
            port_hist_df,
            x=port_hist_df.index,
            y=port_hist_df['y'],
            hover_data={'Value': ':$,.2f', 'y': ':.2f%'},
            markers=True,
        )
        fig.update_yaxes(ticksuffix="%")

        fig.update_layout(transition_duration=500, hovermode='y unified',
                          xaxis=dict(rangeslider=dict(visible=True)))
        fig.update_layout(
            autosize=True,
            margin=dict(l=40, r=20, t=20, b=20),
            legend=dict(orientation="h", yanchor="top", y=-0.15),
        )
        return fig
    except Exception as e:
        print(f"Error in update_port_hist_graph: {e}")
        return go.Figure().update_layout(title=f"Error loading portfolio history: {str(e)}")

@callback(
    Output('winners-table', 'rowData'),
    Output('winners-table', 'columnDefs'),
    Output('winners-table-cards', 'children'),
    Output('losers-table', 'rowData'),
    Output('losers-table', 'columnDefs'),
    Output('losers-table-cards', 'children'),
    Input('interval-dropdown', 'value'))
def update_asset_tables(interval):
    try:
        winners_df = DASH_HANDLER.get_ranked_assets(
            interval, 'price', ascending=False, count=NUM_WINNERS_LOSERS)
        winners_df = winners_df[['Symbol', 'Interval', 'Current Price',
                                 'Price', 'Price % Return']]
        winners_col_defs = [{"field": col, "sortable": True, "filter": True}
                            for col in winners_df.columns]

        losers_df = DASH_HANDLER.get_ranked_assets(
            interval, 'price', ascending=True, count=NUM_WINNERS_LOSERS)
        losers_df = losers_df[['Symbol', 'Interval', 'Current Price',
                               'Price', 'Price % Return']]
        losers_col_defs = [{"field": col, "sortable": True, "filter": True}
                           for col in losers_df.columns]

        winners_rows = winners_df.to_dict('records')
        losers_rows = losers_df.to_dict('records')
        return (winners_rows, winners_col_defs,
                build_mobile_cards(winners_rows, 'Symbol',
                                   MOVERS_MOBILE_FIELDS),
                losers_rows, losers_col_defs,
                build_mobile_cards(losers_rows, 'Symbol',
                                   MOVERS_MOBILE_FIELDS))
    except Exception as e:
        print(f"Error in update_asset_tables: {e}")
        err_col = [{"field": "Error"}]
        err_data = [{"Error": f"Error loading data: {str(e)}"}]
        err_cards = build_mobile_cards(err_data, 'Error', [])
        return (err_data, err_col, err_cards,
                err_data, err_col, err_cards)

@callback(
    Output('portfolio-value-scalar', 'figure'),
    Input('interval-dropdown', 'value'))
def update_portfolio_value(interval):
    try:
        milestone_data = PORTFOLIO_MILESTONES.loc[
            PORTFOLIO_MILESTONES['Interval'] == interval]

        if milestone_data.empty:
            milestone_value = CURRENT_PORTFOLIO_VALUE
        else:
            milestone_value = milestone_data['Value'].values[0]

        port_value_fig = go.Figure()

        port_value_fig.add_trace(go.Indicator(
            mode = "number+delta",
            value = CURRENT_PORTFOLIO_VALUE,
            number = {'valueformat': '$,.2f'},
            delta = {'reference': milestone_value,
                    'relative': False,
                    'position' : "bottom",
                    'valueformat': '$,.2f'}
            ))

        port_value_fig.update_layout(
            height=200,
            margin=dict(l=10, r=10, t=10, b=10),
        )

        return port_value_fig
    except Exception as e:
        print(f"Error in update_portfolio_value: {e}")
        return go.Figure().update_layout(title=f"Error: {str(e)}")

portfolio_tab = dmc.Container(
    [
        dmc.Grid(
            dmc.GridCol(
                html.H1("Portfolio Dashboard"),
                span={"base": 12, "md": 6},
                offset={"base": 0, "md": 3},
            ),
            justify='center',
        ),
        html.Hr(),
        dmc.Grid([
            dmc.GridCol(
                html.Div([
                    "Select period:",
                    dcc.Dropdown(
                        id='interval-dropdown',
                        options=INTERVALS,
                        value=PORTFOLIO_DEFAULT_INTERVAL,
                    ),
                ]),
                # md value unchanged from desktop (1); full width on a phone.
                span={"base": 12, "md": 1},
                offset={"base": 0, "md": 8},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    dcc.Graph(
                        id='portfolio-history-graph',
                        style={"height": "55vh"},
                        config={"responsive": True},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 9},
            ),
            dmc.GridCol(
                dmc.Paper(
                    dmc.Stack([
                        dcc.Graph(
                            id='portfolio-value-scalar',
                            config={"responsive": True},
                        ),
                        responsive_table(
                            'portfolio-milestones-table',
                            primary_field='Interval',
                            mobile_fields=MILESTONE_MOBILE_FIELDS,
                            column_defs=milestones_column_defs,
                            row_data=PORTFOLIO_MILESTONES.to_dict('records'),
                            defaultColDef={"resizable": True},
                            dashGridOptions={"domLayout": "autoHeight"},
                        )],
                        gap="md",
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    responsive_table(
                        'winners-table',
                        primary_field='Symbol',
                        mobile_fields=MOVERS_MOBILE_FIELDS,
                        defaultColDef={"resizable": True},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
                offset={"base": 0, "md": 1},
            ),
            dmc.GridCol(
                dmc.Paper(
                    responsive_table(
                        'losers-table',
                        primary_field='Symbol',
                        mobile_fields=MOVERS_MOBILE_FIELDS,
                        defaultColDef={"resizable": True},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
                offset={"base": 0, "md": 1},
            ),
        ]),
    ],
    fluid=True,
)
