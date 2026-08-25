import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))

# Parse --demo before any tab imports so PORTFOLIO_DEMO_MODE is set in time
import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument('--demo', action='store_true',
                     help='Run dashboard with synthetic demo data (no DB or yfinance calls)')
_args, _ = _parser.parse_known_args()
if _args.demo:
    os.environ['PORTFOLIO_DEMO_MODE'] = '1'
    print("DEMO MODE — using synthetic portfolio data")

import time
enter = time.perf_counter()

import dash_mantine_components as dmc
from dash import Dash, dcc

from libraries.globals import MYSQL_CACHE_ENABLED, MYSQL_CACHE_TTL

if MYSQL_CACHE_ENABLED:
    print(f"Cache enabled with TTL of {MYSQL_CACHE_TTL} seconds")
else:
    print("Cache disabled")

print("Loading Portfolio Dashboard...")
app = Dash(external_stylesheets=dmc.styles.ALL)

from visualization.dash.portfolio_dashboard.tabs import (
                                                         portfolio_tab,
                                                         assets_tab,
                                                         hypotheticals_tab,
                                                         sectors_tab,
                                                         asset_types_tab,
                                                         account_types_tab,
                                                         geography_tab)
from visualization.dash.portfolio_dashboard.tabs.chat_tab import chat_tab

# Import position matters. globals.py instantiates DASH_HANDLER at import time
# and branches on PORTFOLIO_DEMO_MODE, which portfolio_dashboard.py sets from
# --demo at the very top. Importing this above the tab imports would construct
# the real (non-demo) handler and silently break `--demo`.
from visualization.dash.portfolio_dashboard.globals import DASH_HANDLER
from visualization.dash.portfolio_dashboard.banners import build_staleness_banner


print("Loading Portfolio Tabs...")

_tabs = dmc.Tabs(
    value='portfolio-dash-tab',
    id='tabs',
    children=[
        dmc.TabsList([
            dmc.TabsTab('Portfolio', value='portfolio-dash-tab'),
            dmc.TabsTab('Sectors', value='sectors-dash-tab'),
            dmc.TabsTab('Asset Types', value='asset-types-dash-tab'),
            dmc.TabsTab('Account Types', value='account-types-dash-tab'),
            dmc.TabsTab('Geography', value='geography-dash-tab'),
            dmc.TabsTab('Assets', value='assets-dash-tab'),
            dmc.TabsTab('Hypotheticals', value='hypotheticals-dash-tab'),
            dmc.TabsTab('Chat', value='chat-dash-tab'),
        ]),
        # Each data tab gets its own loading spinner. The Chat tab is left
        # WITHOUT one — instead of a full-page spinner during the multi-second
        # LLM call, it shows a lightweight inline "Thinking…" line.
        dmc.TabsPanel(dcc.Loading(portfolio_tab), value='portfolio-dash-tab'),
        dmc.TabsPanel(dcc.Loading(sectors_tab), value='sectors-dash-tab'),
        dmc.TabsPanel(dcc.Loading(asset_types_tab), value='asset-types-dash-tab'),
        dmc.TabsPanel(dcc.Loading(account_types_tab), value='account-types-dash-tab'),
        dmc.TabsPanel(dcc.Loading(geography_tab), value='geography-dash-tab'),
        dmc.TabsPanel(dcc.Loading(assets_tab), value='assets-dash-tab'),
        dmc.TabsPanel(dcc.Loading(hypotheticals_tab), value='hypotheticals-dash-tab'),
        dmc.TabsPanel(chat_tab, value='chat-dash-tab'),
    ],
)

# Static across requests -- no DB call, so it's computed once at import time
# rather than inside _build_layout() below.
_demo_banners = []
if os.environ.get('PORTFOLIO_DEMO_MODE') == '1':
    _demo_banners.append(dmc.Alert(
        "DEMO MODE — All data is synthetic. No real financial information is displayed.",
        color="orange", variant="filled", mb="xs",
    ))


def _build_layout():
    """
    Rebuilt on every page load (app.layout is this function, not a static
    value) so a staleness banner can actually appear without a server
    restart. DASH_HANDLER is built once at process start (see globals.py),
    so DASH_HANDLER.data_as_of/is_stale are frozen at that moment -- if the
    updater then fails for days, a static layout would keep serving a "data
    is fresh" banner state forever. get_freshness() re-queries history_meta
    and current_prices fresh on each call (two indexed SELECTs; negligible
    next to a Dash page render) so a long-running process still reflects an
    updater outage.

    _tabs is reused as-is (built once at import, holds no DB-derived state of
    its own -- each tab factory reads from DASH_HANDLER's already-loaded
    frames), so this stays cheap: no DB work beyond the freshness lookup.
    """
    banners = list(_demo_banners)

    freshness = DASH_HANDLER.get_freshness()
    staleness_banner = build_staleness_banner(
        freshness['data_as_of'], freshness['is_stale'],
        freshness['price_fetched_at'], freshness['is_price_stale'])
    if staleness_banner is not None:
        banners.append(staleness_banner)

    content = dmc.Stack(banners + [_tabs], gap=0) if banners else _tabs
    return dmc.MantineProvider(content)


app.layout = _build_layout

print(f'Portfolio Dashboard loaded in {time.perf_counter() - enter} seconds')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8050))
    app.run(debug=False, port=port)
