from dash import (callback, clientside_callback, dcc, Input, Output, no_update)
import dash_ag_grid as dag
import plotly.express as px
import plotly.graph_objs as go

from libraries.downsample import downsample_history, top_n_symbols
from visualization.dash.portfolio_dashboard.globals import *

import dash_mantine_components as dmc

# Module-level cache populated on first callback execution
_hypo_cache = {}

def _load_hypo_data():
    """Load and cache hypothetical data on first access."""
    if 'normalized_hypo_df' not in _hypo_cache:
        print("Loading hypothetical tab data...")
        normalized_hypo_df = DASH_HANDLER.exits_hypotheticals_history_df
        normalized_hypo_df = DASH_HANDLER.expand_history_df(normalized_hypo_df)
        _hypo_cache['normalized_hypo_df'] = normalized_hypo_df

        sectors = normalized_hypo_df['Sector'].unique().tolist()
        sectors = [{'label': x, 'value': x} for x in sectors]
        sectors = sorted(sectors, key=lambda x: x['label'])
        _hypo_cache['sectors'] = sectors

        asset_hypo_df = DASH_HANDLER.assets_hypothetical_history_df
        asset_hypo_stats_df = DASH_HANDLER.gen_historical_stats(
            asset_hypo_df, hypotheticals=True)
        asset_hypo_stats_df = asset_hypo_stats_df[
            ['Name', 'Symbol', 'Sector', 'Hypo Ret.(Exit/Current)%']]
        _hypo_cache['asset_hypo_stats_df'] = asset_hypo_stats_df
        _hypo_cache['hypo_column_defs'] = [
            {"field": col, "sortable": True, "filter": True}
            for col in asset_hypo_stats_df.columns
        ]
        print("✓ Hypothetical tab data loaded")
    return _hypo_cache


@callback(
    Output('hypo-sector-select-dropdown', 'options'),
    Output('hypos-global-table', 'columnDefs'),
    Output('hypos-global-table', 'rowData'),
    Output('hypothetical-normalized-history-graph', 'figure'),
    Input('tabs', 'value'))
def initialize_hypotheticals_tab(active_tab):
    if active_tab != 'hypotheticals-dash-tab':
        return no_update, no_update, no_update, no_update

    data = _load_hypo_data()
    normalized_hypo_df = data['normalized_hypo_df']

    # The initial view defaults to the biggest movers instead of all 126
    # series (6.4 MB). The sector/asset dropdowns below bring in the rest.
    default_symbols = top_n_symbols(normalized_hypo_df)
    initial_df = normalized_hypo_df[
        normalized_hypo_df['Symbol'].isin(default_symbols)]
    initial_df = downsample_history(initial_df, group_cols=('Symbol',))

    fig = px.line(
        initial_df,
        x=initial_df['Date'],
        y=initial_df['ClosingPrice % Change'],
        color=initial_df['Symbol'],
        line_dash=initial_df['Sector'],
    )
    fig.update_layout(height=800)
    fig.update_yaxes(ticksuffix="%")

    return (
        data['sectors'],
        data['hypo_column_defs'],
        data['asset_hypo_stats_df'].to_dict('records'),
        fig,
    )


@callback(
    Output('asset-select-dropdown', 'options'),
    Input('hypo-sector-select-dropdown', 'value'))
def update_asset_dropdown_options(sectors):
    data = _load_hypo_data()
    normalized_hypo_df = data['normalized_hypo_df']

    if not sectors:
        df = normalized_hypo_df
    else:
        df = normalized_hypo_df[normalized_hypo_df['Sector'].isin(sectors)]

    df = df[['Symbol', 'Name', ]].drop_duplicates()

    assets = []
    for _, row in df.iterrows():
        asset_dict = {
            'value': row['Symbol'],
            'label': f"{row['Symbol']} | {row['Name']}",
        }
        assets.append(asset_dict)

    assets = sorted(assets, key=lambda x: x['label'])

    return assets

# Phase 2: Clientside callback — no server round-trip needed
clientside_callback(
    """
    function(sectors, options) {
        if (!sectors || !sectors.length) return null;
        return options.map(function(x) { return x.value; });
    }
    """,
    Output('asset-select-dropdown', 'value'),
    Input('hypo-sector-select-dropdown', 'value'),
    Input('asset-select-dropdown', 'options'),
)

@callback(
    Output('hypothetical-normalized-history-graph', 'figure', allow_duplicate=True),
    Input('hypo-sector-select-dropdown', 'value'),
    Input('asset-select-dropdown', 'value'),
    prevent_initial_call=True)
def update_normalized_hypo_graph(sectors, assets):
    data = _load_hypo_data()
    normalized_hypo_df = data['normalized_hypo_df']

    if not sectors and not assets:
        # Same default as the initial view: biggest movers, not all 102
        # symbols. An explicit sector/asset selection still gets exactly
        # what was picked.
        default_symbols = top_n_symbols(normalized_hypo_df)
        df = normalized_hypo_df[normalized_hypo_df['Symbol'].isin(default_symbols)]
    else:
        df = normalized_hypo_df[normalized_hypo_df['Symbol'].isin(assets)]

    df = downsample_history(df, group_cols=('Symbol',))

    normalized_hypo_fig = px.line(
        df,
        x=df['Date'],
        y=df['ClosingPrice % Change'],
        color=df['Symbol'],
        line_dash=df['Sector'],
    )

    normalized_hypo_fig.update_layout(transition_duration=500, height=800)
    normalized_hypo_fig.update_yaxes(ticksuffix="%")
    return normalized_hypo_fig

hypotheticals_tab = dmc.Container(
    [
        dmc.Grid([
            dmc.GridCol(
                dcc.Dropdown(
                    options=[],
                    id='hypo-sector-select-dropdown',
                    placeholder='Select sector(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
            dmc.GridCol(
                dcc.Dropdown(
                    id='asset-select-dropdown',
                    placeholder='Select asset(s)',
                    multi=True,
                ),
                span={"base": 12, "md": 3}, offset={"base": 0, "md": 1},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    dcc.Graph(
                        id='hypothetical-normalized-history-graph',
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 9},
            ),
            dmc.GridCol(
                dmc.Paper(
                    dag.AgGrid(
                        id='hypos-global-table',
                        columnDefs=[],
                        rowData=[],
                        defaultColDef={"resizable": True},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
            ),
        ]),
    ],
    fluid=True,
)
