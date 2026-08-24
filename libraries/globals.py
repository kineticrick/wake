import os

QUANTITY_ASSET_EVENTS = ['buy', 'sell', 'split', 'acquisition']
NON_QUANTITY_ASSET_EVENTS = ['dividend']
ASSET_EVENTS = QUANTITY_ASSET_EVENTS + NON_QUANTITY_ASSET_EVENTS

MASTER_LOG_COLUMNS = ['Date', 'Symbol', 'Action', 'Quantity', 
                      'Dividend', 'Multiplier', 'Acquirer']

# NOTE: pandas 2.2+ removed the bare 'M'/'Q'/'Y'/'BM'/'BQ' aliases (ambiguous
# with minute/etc). Month/quarter/year-end now require the explicit '...E' forms
# ('ME', 'QE', 'YE', 'BME', 'BQE', 'BYE'). These are date-offset aliases used by
# date_range()/asfreq()/to_offset(); do NOT pass them to pd.Period(), which
# still uses the period aliases ('M'/'Q'/'Y').
CADENCE_MAP = {
    'daily': 'D',
    'weekly': 'W',
    'monthly': 'ME',
    'quarterly': 'QE',
    'yearly': 'YE',
}

BUSINESS_CADENCE_MAP = {
    'daily': 'B',
    'weekly': 'W-FRI',
    'monthly': 'BME',
    'quarterly': 'BQE',
    'half-yearly': '2BQE',
    'yearly': 'BYE',
}

# Symbols which are not currently listed
SYMBOL_BLACKLIST = [
    'MGP',
    'DRE',
    'STOR',
    'BF.B',
    'CONE',
    'DIDI',
    'QTS',
    'ATVI',
    'PEAK', # Changed to "DOC"
    'SPWR',
    'SQ',
    'LEN', #Returns as 'delisted' though doesnt seem to be
]

MYSQL_CACHE_ENABLED = True  # Enabled for performance (was False)
MYSQL_CACHE_HISTORY_TAG = 'historycaches'
MYSQL_CACHE_TTL = 60*60*4  # 4 hours (was 1 hour) - balance between freshness and performance

# When true, the process must NEVER write to the database or make outbound
# network calls. The Dash web tier runs this way so it can be served from a
# read-only MySQL grant with no internet egress; all history updates happen in
# generators/daily_update.py instead. See
# docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md
PORTFOLIO_READ_ONLY = os.environ.get('PORTFOLIO_READ_ONLY') == '1'

# Upper bound on libraries.helpers._aggregation_cache. Each entry is a fully
# expanded per-asset history frame, so this trades memory for recompute.
AGGREGATION_CACHE_MAX_ENTRIES = 8

# Chart payload shaping. Beyond this many days back, history is thinned to
# weekly: a 40k-point trace carries far more points than the ~1500 horizontal
# pixels a chart actually has, so the difference is invisible.
DOWNSAMPLE_DAILY_WINDOW_DAYS = 365
# How many series the Hypotheticals/Assets charts show before the user opts in
# to more via the existing dropdowns.
DEFAULT_CHART_SERIES = 10
# The Assets chart renders one trace per held (Symbol, AccountType) pair --
# ~34 concurrently by default, an order of magnitude more than the dimension
# tabs' 3-30 traces -- and unlike Hypotheticals it must keep every trace (it
# IS the "what do I hold" view, not a top-movers view). At that trace count,
# DOWNSAMPLE_DAILY_WINDOW_DAYS's 1-year daily window still exceeds the 500 KB
# payload budget, so this chart gets its own, shorter daily window.
ASSETS_DOWNSAMPLE_WINDOW_DAYS = 60

### Generators ###

ROOT_DIR = "/home/kineticrick/code/python/wake"

FILEDIRS = {'entities': os.path.join(ROOT_DIR, 'files/entities'), 
            'splits': os.path.join(ROOT_DIR, 'files/splits'),
            'acquisitions': os.path.join(ROOT_DIR, 'files/acquisitions'),
            'schwab_transactions': os.path.join(ROOT_DIR, 'files/transactions/schwab'),
            'tdameritrade_transactions': os.path.join(ROOT_DIR, 'files/transactions/tdameritrade'),
            'wallmine_transactions': os.path.join(ROOT_DIR, 'files/transactions/wallmine')
            }

TRADES_DICT_KEYS = ['symbol', 'date', 'action',
                    'num_shares', 'price_per_share', 
                    'total_price', 'account_type']

DIVIDENDS_DICT_KEYS = ['symbol', 'date', 'action', 'dividend', 'account_type']

IMPORTER_VERBOSE = True

SCHWAB_CSV_VALID_COLUMNS = [
    'Symbol',
    'Quantity',
    'Cost Basis',
    'Dividend Yield',
] 

ACCOUNT_TYPES = ['Discretionary', 'Retirement']
