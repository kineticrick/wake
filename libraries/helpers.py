#!/usr/bin/env python 
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import bisect
import math
import pandas as pd
import datetime

from collections import defaultdict, OrderedDict
from libraries.db import dbcfg
from libraries.pandas_helpers import print_full, mysql_to_df
from libraries.db.sql import (master_log_buys_query,
                              master_log_buys_columns,
                              master_log_sells_query,
                              master_log_sells_columns,
                              master_log_dividends_query,
                              master_log_dividends_columns,
                              master_log_splits_query,
                              master_log_splits_columns,
                              master_log_acquisitions_query,
                              master_log_acquisitions_columns,
                              read_entities_table_query,
                              read_entities_table_columns,
                              read_summary_table_query, 
                              read_summary_table_columns)
from libraries.yfinance_helpers import get_historical_prices, get_current_price
from libraries.globals import (NON_QUANTITY_ASSET_EVENTS, ASSET_EVENTS,
                            MASTER_LOG_COLUMNS, CADENCE_MAP,
                            ACCOUNT_TYPES, AGGREGATION_CACHE_MAX_ENTRIES)
from pandas.tseries.offsets import BDay
from pandas.tseries.frequencies import to_offset

from diskcache import Cache

cache = Cache('cache')

# Static mapping of event types to their query/column pairs (avoids globals() lookups)
_EVENT_QUERIES = {
    'buy': (master_log_buys_query, master_log_buys_columns),
    'sell': (master_log_sells_query, master_log_sells_columns),
    'dividend': (master_log_dividends_query, master_log_dividends_columns),
    'split': (master_log_splits_query, master_log_splits_columns),
    'acquisition': (master_log_acquisitions_query, master_log_acquisitions_columns),
}

def  build_master_log(symbols: list=[], account_type: str=None) -> pd.DataFrame:
    """
    For each ASSET_EVENT, retrieve log of each event as a dataframe, then 
    merge each into a sorted master log

    If symbols are provided, only retrieve logs for those symbols

    Returns:
        pd.DataFrame: Single, chronologically sorted, master log of all events,
        across all assets 
    """
    # Master log of all events

    master_log_df = pd.DataFrame(columns=MASTER_LOG_COLUMNS)
    
    symbols_clause = \
        "(" + ", ".join([f"'{symbol}'" for symbol in symbols]) + ")"
    
    symbols_str = " symbol IN " + symbols_clause if len(symbols) > 0 else ""
                #    "(" + ", ".join([f"'{symbol}'" for symbol in symbols]) + ")"
                # ) if len(symbols) > 0 else ""

    # Retrieve log of each event as a dataframe, then
    # merge each into a sorted master log
    import warnings
    warnings.simplefilter(action='ignore', category=FutureWarning)

    event_dfs = []
    for event in ASSET_EVENTS:
        query, columns = _EVENT_QUERIES[event]
        if len(symbols) > 0:
            if "WHERE" in query:
                query += " AND "
            else:
                query += " WHERE "
            query += symbols_str
            if event == 'acquisition':
                query += ' OR acquirer IN ' + symbols_clause

        event_log_df = mysql_to_df(query, columns, dbcfg, cached=True)
        event_dfs.append(event_log_df)

    master_log_df = pd.concat([master_log_df] + event_dfs, ignore_index=True)

    # Acquisition events are stored in the master log as two separate events,
    # 'acquisition-target' and 'acquisition-acquirer'.  This allows each party to be 
    # independently and bidirectionally tied to the acquisition
    master_log_df['Action'] = master_log_df['Action'].replace('acquisition', 'acquisition-target')
    acquisition_acquirer_events = []
    
    # Swap "symbol" and "acquirer" columns for acquisition-acquirer events
    # This creates a complementary entry for the "other side" of the acquisition
    for _, acq_event in \
        master_log_df[master_log_df['Action'] == 'acquisition-target'].iterrows():
            acquisition_acquirer_events.append({
                'Date': acq_event['Date'],
                'Symbol': acq_event['Acquirer'],
                'Action': 'acquisition-acquirer',
                'Multiplier': acq_event['Multiplier'],
                'Target': acq_event['Symbol'],
            })
            
    # Merge into master log
    acquisition_acquirer_events_df = pd.DataFrame(acquisition_acquirer_events)
    master_log_df = pd.concat([master_log_df, acquisition_acquirer_events_df], ignore_index=True)

    # If specific symbols are provided, filter master log to only include those symbols
    # This is done because if you query just an acquisition-acquirer symbol, but not the 
    # acquisition-target, you'll still get the target in the log (since both are pulled from the 
    # 'acquisitions' table as part of the same row)
    if len(symbols) > 0:
        master_log_df = master_log_df[master_log_df['Symbol'].isin(symbols)]

    if len(symbols) > 0:
        # Sort by symbol, then date if specific symbols are provided
        sort_clause = ['Symbol', 'Date']
    else:
        # Sort by date, then multiplier if all assets retrieved
        sort_clause = ['Date', 'Multiplier']
    
    master_log_df = master_log_df.sort_values(by=sort_clause, 
                                              ascending=True,
                                              ignore_index=True)
    
    # For actions which are agnostic of a specific brokerage account 
    # (ie splits or acquisitions change the quantity of shares regardless
    # of what other actions have occurred in any specific account. A 2x split affects 
    # ALL accounts)  
    
    master_log_df.loc[
        master_log_df['Action'].isin(['split', 'acquisition-target', 'acquisition-acquirer']),
        'AccountType'] = 'Agnostic'

    # Account-type filtering is applied at the transaction level (per CLAUDE.md):
    # keep this account's own events plus 'Agnostic' events (splits/acquisitions
    # apply to every account). Filtering here — rather than on the per-row
    # AccountType of the computed value series — avoids dropping split-date rows
    # that get tagged 'Agnostic' and ffilled forward.
    if account_type is not None:
        master_log_df = master_log_df[
            master_log_df['AccountType'].isin([account_type, 'Agnostic'])]

    return master_log_df

def gen_hist_quantities(asset_event_log_df: pd.DataFrame, 
                        cadence: str='daily', 
                        expand_chronology: bool=True) -> pd.DataFrame:
    """ 
    Based on log of asset events (buy, sell, split, etc) for a single asset,
    build a dataframe of historical quantities of that asset, on the 
    cadence given (daily, weekly, monthly, quaterly, yearly). 
    
    If expand_chronology is True, then the dataframe will include all dates.
    If False, then only dates with a quantity change will be included.
    
    Returns: quantities_df
        Date, Symbol, quantity (net), AccountType
        2019-01-01, MSFT, 100, Discretionary
        2019-06-12, MSFT, 50, Discretionary
    """
    #TODO: Ensure only one symbol, account type pair is passed in
    # assert(len(asset_event_log_df['Symbol'].unique()) == 1)

    # assert(len(asset_event_log_df[['Symbol', 'AccountType']].drop_duplicates()) == 1)
    
    # Remove non-quantity events, like dividend 
    # ('~' is the bitwise NOT operator)
    asset_event_log_df = asset_event_log_df[
        ~asset_event_log_df['Action'].isin(NON_QUANTITY_ASSET_EVENTS)]
    
    asset_event_log_df = \
        asset_event_log_df.sort_values(by=['Date'], ascending=True,)
        
    # Process each event and depending on the action, update the total quantity
    # all_events = []
    # Indexed by date to ensure that we only have one row per date
    all_events = defaultdict(dict)
    total_quantity = 0
    
    # Initialize objects for cost basis determination
    cost_basis = 0
    # Will be list of dicts, where dicts will hold: 
    #   Date: Date of purchase
    #   initial_quantity: quantity of shares purchased
    #   remaining_quantity: quantity of shares remaining from this purchase tranche
    #   purchase_price: price per share at time of purchase
    purchase_list = []
    for event in asset_event_log_df.itertuples(index=False):
        date = event.Date
        symbol = event.Symbol
        account_type = getattr(event, 'AccountType', None)

        all_events[date]['Date'] = date
        all_events[date]['Symbol'] = symbol
        all_events[date]['AccountType'] = account_type

        action = event.Action
        quantity = event.Quantity
        multiplier = event.Multiplier
        price_per_share = event.PricePerShare
        
        match action:
            case 'buy':
                total_quantity += quantity
                
                # Add to cost basis
                cost_basis += quantity * price_per_share
                
                # Insert into purchase list sorted by date (O(log n) vs O(n log n) re-sort)
                entry = {
                    'Date': date,
                    'initial_quantity': quantity,
                    'remaining_quantity': quantity,
                    'purchase_price': price_per_share
                }
                bisect.insort(purchase_list, entry, key=lambda x: x['Date'])
                
            case 'sell':
                total_quantity -= quantity
                
                # Handle Cost Basis adjustment
                # Sell shares from oldest purchase first, then proceed to next purchase 
                # until all sold shares are accounted for
                for purchase in purchase_list:
                    # If the amount sold is larger than what is in this purchase tranche
                    # then remove the entire remaining purchase tranche 
                    # and subtract from cost basis 
                    if quantity >= purchase['remaining_quantity']:
                        quantity -= purchase['remaining_quantity']
                        cost_basis -= \
                            purchase['remaining_quantity'] * purchase['purchase_price']
                        purchase['remaining_quantity'] = 0
                    # If the amount sold is smaller than what is in this purchase tranche, 
                    # then reduce tranche by amount sold and update cost basis
                    else:
                        cost_basis -= quantity * purchase['purchase_price']
                        purchase['remaining_quantity'] -= quantity
                        break
                
            case 'split':
                total_quantity *= multiplier
                
                # Handle cost basis
                for purchase in purchase_list:
                    purchase['initial_quantity'] *= multiplier
                    purchase['remaining_quantity'] *= multiplier
                    purchase['purchase_price'] /= multiplier

            case 'acquisition-target':
                total_quantity = 0
                
            case 'acquisition-acquirer':
                # Get the quantity of the acquisition target asset on the date of the acquisition
                target = [getattr(event, 'Target', None)]
                day_before = date - BDay(1)
                target_prior_quantity_df = \
                    get_asset_quantity_by_date(target, day_before.strftime('%Y-%m-%d'))

                target_prior_quantity = target_prior_quantity_df['Quantity']
                target_prior_cost_basis = target_prior_quantity_df['CostBasis']

                # Add the quantity of the target asset * multiplier to the acquirer's total
                target_prior_quantity *= multiplier
                target_prior_quantity = math.floor(target_prior_quantity)
                total_quantity += target_prior_quantity
                cost_basis += target_prior_cost_basis
    
        all_events[date]['Quantity'] = total_quantity
        all_events[date]['CostBasis'] = cost_basis
        all_events[date]['AccountType'] = account_type
    all_events = list(all_events.values())        
    quantities_df = pd.DataFrame(all_events)
    
    # Set date as DateTimeIndex
    quantities_df['Date'] = pd.to_datetime(quantities_df['Date'])

    if expand_chronology:
        first_date = quantities_df['Date'].iloc[0]

        if quantities_df['Quantity'].iloc[-1] == 0: 
            # If there are no more shares held, use the last date held 
            last_date = quantities_df['Date'].iloc[-1]

        else:
            # If there are still shares held, use today as the last date
            last_date = pd.to_datetime('today').date()

        # Advance the last date to the end of the last date's period, to capture
        # all actions. CADENCE_MAP holds date-offset aliases (ME/QE/YE/...), so
        # roll forward to the period boundary instead of using pd.Period (which
        # would require the incompatible period aliases M/Q/Y).
        offset = to_offset(CADENCE_MAP[cadence])
        last_date = offset.rollforward(pd.Timestamp(last_date)).date()

        # Fill in dataframe with every date in the range
        date_range = pd.date_range(start=first_date, end=last_date, 
                                freq='D')

        quantities_df = quantities_df.set_index('Date').reindex(date_range)

        # Fill in missing values with previous value
        # IE Set quantity to last/current, as of that date
        quantities_df['Quantity'] = quantities_df['Quantity'].ffill()
        quantities_df['Symbol'] = quantities_df['Symbol'].ffill()
        quantities_df['CostBasis'] = \
            quantities_df['CostBasis'].ffill()
        quantities_df['AccountType'] = \
            quantities_df['AccountType'].ffill()
        
        # Downsample to specified cadence
        # quantities_df = quantities_df.asfreq(BUSINESS_CADENCE_MAP[cadence])
        
    if "Date" in quantities_df.columns: 
        quantities_df = quantities_df.set_index('Date')

    return quantities_df

def gen_hist_quantities_mult(assets_event_log_df: pd.DataFrame, 
                             cadence: str='daily', 
                             expand_chronology: bool=True) -> pd.DataFrame:
    """
    Given an event log of multiple assets, generate a dataframe of historical
    quantities of each asset, on the cadence given (daily, weekly, monthly, etc)
    If only need to process a single asset, use gen_hist_quantities()
    
    If expand_chronology is True, then the dataframe will include all dates.
    If False, then only dates with a quantity change will be included.
    
    Returns: quantities_df 
    Date, Symbol, Action, Quantity (net)
    2019-01-01, MSFT, buy, 100
    2019-06-12, MSFT, sell, 50
    2020-02-07, DIS, buy, 75
    2020-08-22, DIS, buy, 20
    """
    
    # Get list of unique [Symbol, AccountType] pairs
    symbols = assets_event_log_df[['Symbol', 'AccountType']].drop_duplicates()

    symbols = symbols[symbols['AccountType'].isin(['Discretionary', 'Retirement'])]

    quantities_df = pd.DataFrame()
    # Get historical quantities for each symbol
    for _, row in symbols.iterrows():
        symbol = row['Symbol']
        account_type = row['AccountType']
        symbol_event_log_df = assets_event_log_df[
            (assets_event_log_df['Symbol'] == symbol) & 
            ((assets_event_log_df['AccountType'] == account_type) | 
             (assets_event_log_df['AccountType'] == 'Agnostic'))]
        symbol_quantities_df = gen_hist_quantities(symbol_event_log_df,
                                                   cadence=cadence,
                                                   expand_chronology=expand_chronology)
        # The per-event AccountType ffills 'Agnostic' (splits/acquisitions) across a
        # series; overwrite with this series' true account so every row is cleanly
        # labeled. Required for the (date, symbol, account_type) primary key, and it
        # also removes the spurious 'Agnostic' bucket from the AccountType dimension.
        symbol_quantities_df['AccountType'] = account_type
        quantities_df = pd.concat([quantities_df, symbol_quantities_df])

    return quantities_df

def get_asset_quantity_by_date(symbols: list, date: str) -> pd.DataFrame:
    """
    Get quantity of each asset in portfolio, as of a given date
    """
    #TODO: Assert date format
    
    asset_events_log = build_master_log(symbols)
    hist_quantities_df = gen_hist_quantities_mult(asset_events_log, 'daily')
    
    return hist_quantities_df.loc[date]

def is_bday(date: str) -> bool:
    """
    Check if a date is a business day
    """
    bday = BDay()
    return bday.is_on_offset(pd.to_datetime(date))
    
def gen_assets_historical_value(symbols: list=[],
                                cadence: str='daily',
                                start_date: str=None,
                                include_exit_date=True,
                                account_type: str=None) -> pd.DataFrame:
    """ 
    Provide lifetime value of asset within portfolio over time
    Takes quantity of shares, and asset price at that time, to calculate
    Defaults to daily cadence, unless specified 
    
    Can be used to generate historical daily value of every asset ever owned 
    (and, by extension, full portfolio)
    
    If "include_exit_date" is True, then the last date of the asset's possession, when 
    quantity goes to 0, will be included.  If False, then the last date will be the
    final date on which a non-zero amount of shares was held at close of the day. 
    
    Returns: merged_df -> 
    Date, Symbol, Quantity, CostBasis, Close, Value (Quantity * Close), PercentReturn (CostBasis vs Value)
    2019-01-01, MSFT, 100, 7657, 67, 6700, 57.12
    2019-01-02, MSFT, 100, 7657, 68, 6800, 57.58
    """
    assert(cadence in CADENCE_MAP.keys())
    assert(type(symbols) == list)
    
    # Get master event log for asset
    assets_event_log_df = build_master_log(symbols, account_type=account_type)

    # Get historical share quantities of assets
    quantities_df = gen_hist_quantities_mult(assets_event_log_df, 
                                              cadence=cadence)
    
    if start_date is not None:
        start_date = pd.to_datetime(start_date)
        
        # If start_date is not a business/trading day, then create a temporary
        # start date which is the previous business day before the start date
        # This allows us to fill in the non-business days gap with the previous
        # trading days information 
        # (IE, if start date is a Sunday, then we will set Friday as temp start day
        # and fill in the weekend with Friday's data)
        orig_start_date = start_date
        
        # FYI, Possible that both start_date and orig_start_date
        # are the same. But after this, we now know that start_date 
        # will always be a business day
        start_date = BDay().rollback(orig_start_date)
        
        quantities_df = \
            quantities_df[quantities_df.index >= start_date]

    sorted_quantities_df = \
        quantities_df.sort_index(ascending=True)
        
    # Get first and last dates to query historical prices for all assets
    # Once we have superset of all dates for all assets, can pull subset
    # for just the dates we owned the asset. Much faster to pull all prices
    # with a single query, than to query for each asset with specific dates
    first_date = sorted_quantities_df.index[0]
    last_date = sorted_quantities_df.index[-1]

    # For an account_type-filtered call, fetch prices for the FULL (unfiltered)
    # symbol universe so the filtered and unfiltered calls share the same
    # price-cache bucket. yfinance's batch download returns 1-cent-different
    # closes for the same ticker+date depending on which symbols are batched
    # together; sharing the bucket is what keeps the account-split breakdown
    # exactly reconcilable with the unfiltered total (Disc + Ret == full).
    # The inner merge below restricts back to the symbols actually held.
    # When unfiltered, assets_event_log_df is already the full log — reuse it
    # instead of rebuilding (this is the hot path used throughout startup).
    # Note: this penny-exact reconciliation guarantee is specific to the
    # account_type split. An entity-filtered call (a non-empty `symbols` subset
    # with account_type=None) deliberately prices only its subset and is not
    # expected to reconcile against the unfiltered total — it only needs to be
    # internally consistent.
    if account_type is None:
        full_symbols_log = assets_event_log_df
    else:
        full_symbols_log = build_master_log(symbols)
    all_symbols = list(full_symbols_log['Symbol'].unique())

    # Get historical prices of assets
    prices_df = get_historical_prices(tickers=all_symbols,
                                start=first_date, end=last_date,
                                interval=cadence, cleaned_up=True)

    # Standardize quantities and prices dataframes
    quantities_df = quantities_df.reset_index()
    quantities_df = quantities_df.rename(columns={'index': 'Date'})

    # Merge quantities and prices
    merged_df = quantities_df.merge(
        prices_df, on=['Date','Symbol'], how='inner')    
        
    # Calculate value of asset at each date
    merged_df['Value'] = merged_df['Quantity'] * merged_df['ClosingPrice']
    
    # Round to 2 decimal places. Restrict to the numeric columns: the frame
    # also carries a datetime 'Date', which round() never applied to anyway
    # (pandas 2 skipped it silently, pandas 3 warns).
    numeric_cols = merged_df.select_dtypes(include='number').columns
    merged_df[numeric_cols] = merged_df[numeric_cols].round(2)
    
    # Fill in missing values with previous value, to cover weekends + holidays
    merged_df = merged_df.ffill()
    
    # Remove, if any, rows which precede the original start date 
    if start_date is not None:
        merged_df = merged_df[merged_df['Date'] >= orig_start_date]
        
    # Do not return any data for "today", as the day has not closed and 
    # this is not "historical" data yet. Use "get_current_prices" for current prices
    # So, delete rows with todays date
    today = datetime.datetime.today().strftime('%Y-%m-%d')
    merged_df = merged_df[merged_df['Date'] != today]
    
    # Generate daily return column based on growth from cost basis to current value
    merged_df['PercentReturn'] = \
        (merged_df['Value'] - merged_df['CostBasis']) / merged_df['CostBasis'] * 100
    
    # Remove all rows with quantity=0 (ie days on which the asset was sold and went to 0)
    # Should only be 1 row per exited asset 
    if not include_exit_date: 
        merged_df = merged_df[merged_df['Quantity'] != 0]

    return merged_df


def aggregate_assets_history_by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse per-(date, symbol, account) asset history to per-(date, symbol).

    Sums Quantity/CostBasis/Value, keeps ClosingPrice (identical per ticker/date),
    and recomputes PercentReturn from the summed totals. Idempotent: a frame
    already at per-symbol grain passes through unchanged. Used by readers that
    want per-symbol totals while the stored table keeps per-account rows.
    """
    if df.empty:
        return df
    out = df.groupby(['Date', 'Symbol'], as_index=False).agg(
        Quantity=('Quantity', 'sum'),
        CostBasis=('CostBasis', 'sum'),
        ClosingPrice=('ClosingPrice', 'first'),
        Value=('Value', 'sum'),
    )
    # Vectorized: .where(cb != 0) makes zero-cost rows NaN, which fillna(0.0)
    # then turns into 0.0 — matching the old `if r['CostBasis'] else 0.0` guard
    # without the 457k-call row-wise apply (0.83s -> 0.03s on ~114k rows).
    cost_basis = out['CostBasis']
    out['PercentReturn'] = (
        (out['Value'] - cost_basis) / cost_basis.where(cost_basis != 0) * 100
    ).fillna(0.0)
    out[['CostBasis', 'ClosingPrice', 'Value', 'PercentReturn']] = \
        out[['CostBasis', 'ClosingPrice', 'Value', 'PercentReturn']].round(2)
    return out

# LRU-bounded. Previously an unbounded dict: in a long-running hosted process
# with varying filter parameters it grew without limit.
_aggregation_cache = OrderedDict()

def gen_aggregated_historical_value(dimension: str,
                                    symbols: list=[],
                                    cadence: str='daily',
                                    start_date: str=None,
                                    account_type: str=None) -> pd.DataFrame:
    """
    Generate historical value of portfolio, aggregated by a given dimension
    Aggregation is done by taking the average of [cadence] percent return values of
    all member assets within the sector
    (IE: AAPL return over its lifetime, from purchase to today,
    averaged with MSFT's, GOOG's, etc)
    Dimension can be 'sector' or 'asset_type' or 'account_type'

    Returns: aggregated_df ->
    Date, [Dimension], total_value, total_cost_basis
    2016-02-17  Aerospace + Defense    12500.00   9000.00
    2016-02-18  Aerospace + Defense    12750.00   9000.00
    """
    assert(type(symbols) == list)
    assert(dimension in ['Sector', 'AssetType', 'AccountType', 'Geography'])
    assert(cadence in CADENCE_MAP.keys())

    # Check cache for the expensive expanded_df (shared across dimension calls)
    cache_key = (tuple(symbols), cadence, str(start_date), account_type)
    if cache_key in _aggregation_cache:
        _aggregation_cache.move_to_end(cache_key)
    else:
        # Get all assets' historical values
        assets_history_df = gen_assets_historical_value(symbols=symbols,
                                                        cadence=cadence,
                                                        start_date=start_date,
                                                        include_exit_date=False,
                                                        account_type=account_type)
        # Add in Sector, AssetType, etc columns
        _aggregation_cache[cache_key] = add_asset_info(assets_history_df, truncate=False)
        while len(_aggregation_cache) > AGGREGATION_CACHE_MAX_ENTRIES:
            _aggregation_cache.popitem(last=False)

    expanded_df = _aggregation_cache[cache_key]

    # Aggregate by dimension and date by SUMMING dollars (value-weighted),
    # instead of averaging per-asset percent returns. Storing the dollar
    # primitives lets the charts derive lifetime returns AND rebase any window.
    aggregated_df = expanded_df.groupby(['Date', dimension]).agg(
        total_value=('Value', 'sum'),
        total_cost_basis=('CostBasis', 'sum'),
    ).reset_index()
    aggregated_df = aggregated_df.sort_values(by=[dimension, 'Date'],
                                              ascending=True)

    return aggregated_df

def compute_dimension_breakdown(aggregated_df: pd.DataFrame, dimension: str,
                                lifetime: bool) -> pd.DataFrame:
    """Collapse an aggregated dimension time series into a per-member summary.

    Input columns: Date, <dimension>, total_value, total_cost_basis.
    Output columns: <dimension>, 'Current Value', 'VW Return'.

    - lifetime=True:  VW Return = (value - cost) / cost * 100   (cost-based)
    - lifetime=False: VW Return = (last_value / first_value - 1) * 100 (rebased)
    Current Value is the latest total_value for the member.
    """
    cols = [dimension, "Current Value", "VW Return"]
    if aggregated_df.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for member, grp in aggregated_df.sort_values("Date").groupby(dimension):
        last_value = grp["total_value"].iloc[-1]
        if lifetime:
            last_cost = grp["total_cost_basis"].iloc[-1]
            vw_return = (last_value - last_cost) / last_cost * 100 \
                if last_cost else 0.0
        else:
            first_value = grp["total_value"].iloc[0]
            vw_return = (last_value / first_value - 1) * 100 \
                if first_value else 0.0
        rows.append({dimension: member, "Current Value": round(last_value, 2),
                     "VW Return": round(vw_return, 2)})

    return pd.DataFrame(rows, columns=cols)

def get_portfolio_summary() -> pd.DataFrame:
    """ 
    Retrieve summary table of entire portfolio

    Returns: 
        portfolio_summary_df: Symbol, Name, Quantity, Cost Basis, 
                              First Purchase Date, Last Purchase Date, 
                              Total Dividend, Dividend Yield 
    """
    
    portfolio_summary_df = mysql_to_df(read_summary_table_query, 
                                       read_summary_table_columns, dbcfg, 
                                       cached=True)
    
    return portfolio_summary_df

# Module-level cache for entity data (eliminates redundant DB deserializations)
_entities_df_cache = None

def _get_entities_df() -> pd.DataFrame:
    """Load entities once, return a copy each call (copy needed because truncation mutates)."""
    global _entities_df_cache
    if _entities_df_cache is None:
        _entities_df_cache = mysql_to_df(
            read_entities_table_query, read_entities_table_columns,
            dbcfg, cached=True)
    return _entities_df_cache.copy()

def add_asset_info(asset_df: pd.DataFrame, truncate=True) -> pd.DataFrame:
    """
    Given a dataframe of assets, add additional information* about each asset
    Info = Company Name, Sector, AssetType (Common Stock, ETF, REIT), Geography

    If truncate is True, all strings will be truncated to 25 characters

    Returns: asset_df
        {Original DF}, Company Name, Sector, AssetType
    """

    assert('Symbol' in asset_df.columns)

    asset_info_df = _get_entities_df()

    # Truncates long strings, for better display. Both dtypes are named
    # explicitly: these columns are pandas 3 'str', which include='object'
    # only matches via a back-compat shim that pandas will drop.
    if truncate:
        for col in asset_info_df.select_dtypes(include=['object', 'str']):
            asset_info_df[col] = asset_info_df[col].str.slice(0, 25)

    asset_df = asset_df.merge(asset_info_df, on='Symbol', how='left')

    return asset_df

# Cache for 1 hour, since it takes a bit of time to get current prices for 
# all assets in portfolio
# @cache.memoize(expire=60*60*1)    
def get_portfolio_current_value() -> tuple[pd.DataFrame, float]:
    """ 
    Retrieve total value of entire portfolio at current time
    Returns:
        summary_df: (See get_portfolio_summary()) +
            Current Price, Current Value, [Asset Info], 
            % of total value, Lifetime Return (Current Value / Cost Basis)
        total_value: Total value of portfolio, as float
    """
    
    summary_df = get_portfolio_summary()
    symbols = list(summary_df['Symbol'].unique())
    
    current_prices = get_current_price(symbols)

    summary_df = summary_df.merge(current_prices, on='Symbol', how='left')
    summary_df['Current Value'] = \
        round(summary_df['Quantity'] * summary_df['Current Price'], 2)

    # Generate total value of portfolio
    total_value = round(summary_df['Current Value'].sum(), 2)
    
    # Add each asset's % of total value
    summary_df['% Total Portfolio'] = \
        round(summary_df['Current Value'] / total_value * 100, 2)
        
    # Add asset info
    summary_df = summary_df.drop(columns=['Name'])
    summary_df = add_asset_info(summary_df, truncate=True)
    
    # Add lifetime return
    summary_df['Lifetime Return'] = \
        round((summary_df['Current Value'] - summary_df['Cost Basis']) / 
              summary_df['Cost Basis'] * 100, 2)
    
    return (summary_df, total_value)


# TODO: BUILDIN ERROR HANDLING
# Symbols that don't exist
# Symbols that don't have any data
