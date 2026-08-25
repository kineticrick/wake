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
# market-data network calls. The Dash web tier runs this way so it can be
# served from a read-only MySQL grant with no market-data egress; all history
# updates happen in generators/daily_update.py instead. See
# docs/superpowers/specs/2026-08-24-read-only-web-tier-design.md
#
# NOTE: this does not cover every network call the web tier makes -- the Chat
# tab deliberately calls the Anthropic API (libraries/chat/provider.py) on
# every message send. That call is declared, intentional, and unrelated to
# the yfinance/market-data guarantee this flag enforces.
PORTFOLIO_READ_ONLY = os.environ.get('PORTFOLIO_READ_ONLY') == '1'


class ReadOnlyModeError(RuntimeError):
    """
    Raised when code that would fetch live market data (yfinance) runs while
    PORTFOLIO_READ_ONLY is set. This is the read-only tier's enforcement
    mechanism for any fetch path that isn't otherwise gated by a history
    handler's read_only flag -- e.g. the chat layer's on-demand,
    account-filtered recomputation, which cannot be served from the stored
    (unfiltered) dimension tables and would otherwise fall through to a live,
    multi-second-to-minute yfinance fetch inside a request.
    """


# Price-snapshot staleness threshold for the dashboard banner (IMPORTANT 3).
# generators/price_snapshot.py refreshes current_prices every 15 minutes
# during market hours, so under normal operation the newest snapshot is never
# more than ~15-30 minutes old. But the snapshot job does NOT run overnight or
# on weekends (no market hours), so a naive "must be < N minutes old" check
# would false-alarm every single morning and every weekend. A plain Friday
# close (~4pm) to Monday open (~9:30am) gap is already ~65.5 hours; 72 hours
# (3 days) comfortably covers that ordinary weekend gap without a false
# alarm, while still catching a snapshot timer that has actually been dead
# for multiple days (the failure this banner exists to surface).
PRICE_SNAPSHOT_STALE_HOURS = 72

# Upper bound on libraries.helpers._aggregation_cache. Each entry is a fully
# expanded per-asset history frame, so this trades memory for recompute.
AGGREGATION_CACHE_MAX_ENTRIES = 8

# Target points per chart series. Downsampling thins each series to at most
# this many points with an even stride, always keeping its first and last.
#
# 300 is chosen against the payload ceiling, not by feel. The Assets tab is
# the binding constraint (34 traces): at 300/series it lands ~430 KB, better
# than the 455 KB the old 60-day calendar window produced; at 350 it would
# exceed the 500 KB target. Sectors comes out ~219 KB against today's 220 KB,
# i.e. visually identical.
#
# The point of a budget rather than a calendar window is that it does not
# drift: the old window grew the Assets payload ~70 KB per year of history.
CHART_POINT_BUDGET = 300
# How many series the Hypotheticals/Assets charts show before the user opts in
# to more via the existing dropdowns.
DEFAULT_CHART_SERIES = 10

### Generators ###

# Repo root, derived from this file's location rather than hardcoded.
#
# This was previously a literal absolute path to one developer's checkout.
# Everything machine-specific hangs off it -- FILEDIRS (the CSV inputs) and
# CACHE_DIR -- so on any other machine, or in a second checkout on the same
# machine, it did not fail loudly: it silently read inputs from and wrote the
# cache into the *other* directory. Deriving it means a clone works wherever
# it lands. WAKE_ROOT overrides it for deployments that split code and data.
ROOT_DIR = os.environ.get(
    'WAKE_ROOT',
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Absolute path to the diskcache directory, overridable for deployment.
#
# This used to be Cache('cache') in three separate modules, which resolves
# RELATIVE TO THE PROCESS'S WORKING DIRECTORY. Two consequences, both bad:
# launching the app from anywhere but the repo root silently created a second,
# disjoint cache (so the web tier and the systemd jobs -- which do pin
# WorkingDirectory -- could disagree), and the location was wherever the
# launcher happened to be, including directories other users can write.
#
# That second point matters because diskcache deserializes cached values with
# pickle (CVE-2025-69872, GHSA-w8v5-vhqr-4h9v). There is no patched release:
# 5.6.3 is both the vulnerable ceiling and the newest version, published
# 2023-08-31. Anyone who can write into this directory can therefore execute
# code in the process that reads it, so the directory's location and its
# permissions ARE the mitigation. ensure_cache_dir() below restricts it to
# the owner.
CACHE_DIR = os.environ.get('PORTFOLIO_CACHE_DIR',
                           os.path.join(ROOT_DIR, 'cache'))


def ensure_cache_dir(path: str = None) -> str:
    """
    Return the cache directory, creating it and restricting it to its owner.

    Called at import by every module that opens the diskcache. Idempotent, and
    deliberately non-fatal if the mode cannot be changed -- a cache directory
    owned by another user still works for reads, and refusing to start would
    be a worse failure than running with the permissions we found.
    """
    path = path or CACHE_DIR
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


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
