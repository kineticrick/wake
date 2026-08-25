#!/usr/bin/env python3

import logging
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import pandas as pd
import yfinance as yf

from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from libraries.pandas_helpers import print_full, mysql_to_df
from libraries.globals import (BUSINESS_CADENCE_MAP, CADENCE_MAP,
                               SYMBOL_BLACKLIST, PORTFOLIO_READ_ONLY,
                               ReadOnlyModeError, ensure_cache_dir)
from libraries.db import dbcfg

logger = logging.getLogger(__name__)
from libraries.db.sql import (read_current_prices_query,
                              read_current_prices_columns,
                              read_latest_closing_prices_query,
                              read_latest_closing_prices_columns)
from pandas.tseries.offsets import Day

from diskcache import Cache

cache = Cache(ensure_cache_dir())

# Given a type of unit and count, return the start date in the past
# For instance, "week, 2" would give the date 2 weeks ago
def get_dates_from_desc(unit, count):
    # Make sure unit is plural, to fulfill relativedelta arg requirements
    unit = unit + "s" if unit[-1] != "s" else unit
    
    assert(unit in ["days", "weeks", "months", "quarters", "years"])
    assert(isinstance(count, int) and count > 0)
    
    if unit == "quarters":
        unit = "months"
        count = count * 3
    
    today = date.today()
    date_shift_args = {unit: count * -1}
    shift = relativedelta(**date_shift_args)
    
    return today + shift

def get_tickers_from_yfinance(tickers: list) -> dict:
    """ 
    Base retrieval function to get ticker data from yfinance
    Ensures blacklist is used, and generally standardizes all ticker calls to yfinance
    
    Returns: 
        prices(dict): {symbol: yfinance Ticker Object}

    """
    assert(isinstance(tickers, list))
    
    # Remove any symbols which no longer exist 
    # Passing a nonexistent symbol to yfinance will cause 
    # an exception which cannot be handled gracefully
    tickers = list(set(tickers) - set(SYMBOL_BLACKLIST))
    ticker_str = " ".join(tickers)
    
    ticker_info = {} 
    if len(tickers) == 1: 
        ticker = yf.Ticker(ticker_str)
        symbol = ticker.info['symbol']
        ticker_info[symbol] = ticker
    elif len(tickers) > 1:
        ticker_objs = yf.Tickers(ticker_str)
        for sym,obj in ticker_objs.tickers.items():
            try: 
                symbol = obj.info['symbol']
            except: 
                symbol = obj.info['symbol']
            ticker_info[symbol] = obj
    
    return ticker_info

@cache.memoize(expire=60*60*24)
def _gen_historical_prices(tickers, start, end): 
    """ 
    Helper function to retrieve historical prices for a list of ticker objects
    
    Separated out from get_historical_prices to allow for caching/memoization
    """
    ticker_objs = get_tickers_from_yfinance(tickers)
    
    prices = {}
    for symbol, ticker_obj in ticker_objs.items():

        history_df = ticker_obj.history(start=start, end=end,
                                          actions=False, timeout=60)

        history_dict = history_df.to_dict(orient='index')
        prices[symbol] = history_dict

    return prices

def get_historical_prices(tickers: list, start: str=None, 
                   end: str=None, interval: str=None, 
                   cleaned_up: bool=True) -> pd.DataFrame:
    """ 
    # For any given ticker, retrieve market data for the given time period and interval
    # 
    # If "cleaned_up" is set, df will be returned with only closing price, and
    # non-trading days filled in (weekends, holidays)
    #
    # Returns:
    #   prices_df: Date, Open, High, Low, Close, Volume, Symbol 
    #   priced_df(cleaned up): Date, Symbol, ClosingPrice
    """
    if PORTFOLIO_READ_ONLY:
        raise ReadOnlyModeError(
            "Market-data fetches are disabled in read-only mode "
            "(PORTFOLIO_READ_ONLY=1). This process is not allowed to call "
            "yfinance. History is refreshed by generators/daily_update.py, "
            "which runs on a schedule outside the web tier -- run it there "
            "to bring stored history up to date.")

    prices_df = pd.DataFrame()

    # Get historical price data for each ticker
    prices = _gen_historical_prices(tickers, start, end)
    
    # Add date index and symbol column to each dataframe
    for symbol, data in prices.items(): 
        # Need to convert data back to a dataframe
        data = pd.DataFrame.from_dict(data, orient='index')
        
        if data.empty:
            print(f"WARNING: No historical price data (YFinance) found for {symbol}. Ignoring...")
            continue
        
        data.index.name = 'Date'
        
        data['Symbol'] = symbol
        data = data.reset_index()
        data['Date'] = pd.to_datetime(data['Date']).dt.date

        if cleaned_up:
            # Keep only closing price column 
            data = data.rename(columns={'Close': 'ClosingPrice'})
            data = data[['Date', 'Symbol', 'ClosingPrice']]

            # Expand to capture all days (weekends, holidays, etc)
            first_date = data['Date'].min()
            # Use 'end' as the final date, to account for weirdness where the final date of 
            # holding is on a monday, and the last trading day is the friday before
            # This prevents big gaps in the merged df when it joins 
            last_date = end if end is not None else date.today()
            
            # Don't include data on today for "historical" data. Set the final day as yesterday
            today = datetime.today()
            if last_date == today.date():
                last_date = last_date - Day(1)
            
            date_range = pd.date_range(start=first_date, end=last_date, freq='D')
            data = data.set_index('Date').reindex(date_range)
            
            # Fill in gaps with previous day's data
            data[['Symbol', 'ClosingPrice']] = \
                data[['Symbol','ClosingPrice']].ffill()
                
            cadence = CADENCE_MAP[interval]
        else: 
            data = data.set_index('Date')
            cadence = BUSINESS_CADENCE_MAP[interval]
                
        data = data.asfreq(cadence)
        data = data.dropna()

        prices_df = pd.concat([prices_df, data], ignore_index=False)

    # Every symbol came back empty. This is normal when the requested window
    # contains no completed trading sessions — e.g. the gap between the last
    # stored history date and today spans only weekends and/or market holidays.
    # (BaseHistoryHandler uses pandas BDay, which is holiday-unaware, so it can
    # ask for a window like Fri-Juneteenth -> Mon that has no trading days.)
    # The raw empty frame here would carry only a 'Date' column and later crash
    # gen_assets_historical_value with a cryptic "KeyError: 'Symbol'" on the
    # merge. Return a correctly-shaped empty frame instead so the caller's merge
    # is a clean no-op (no new history is added until real data exists).
    if prices_df.empty:
        print(f"WARNING: YFinance returned no historical prices for {tickers} "
              f"between {start} and {end} (likely no trading days in range). "
              f"No new history added.")
        return pd.DataFrame(columns=['Date', 'Symbol', 'ClosingPrice'])

    prices_df = prices_df.round(2)
    prices_df = prices_df.reset_index()
    prices_df = prices_df.rename(columns={'index': 'Date'})

    return prices_df

@cache.memoize(expire=60*15)
def _gen_current_prices(tickers: list) -> list:
    """ 
    Given list of tickers, return current/realtime price data
    
    Separated this out from get_current_prices to allow it to be cached/memoized
    (output must be JSON serializable/pickle-able)
    
    Returns: current_prices (list of dicts)
        
    """
    current_prices = []
    
    ticker_objs = get_tickers_from_yfinance(tickers)
    for symbol, obj in ticker_objs.items():
        try: 
            current_price = obj.info['currentPrice']
        except KeyError:
            current_price = obj.info['dayHigh']
        current_prices.append({
            'Symbol': symbol,
            'Current Price': current_price
        })
        
    return current_prices

def read_current_prices_from_db(tickers: list) -> pd.DataFrame:
    """
    Read the price snapshot written by generators/price_snapshot.py.

    Any ticker with no snapshot row (first deploy, or bought since the last
    snapshot) falls back to its most recent closing price, so the portfolio
    value degrades to "as of last close" rather than becoming NaN.

    Returns: Symbol, Current Price -- restricted to `tickers`.
    """
    prices_df = mysql_to_df(read_current_prices_query,
                            read_current_prices_columns, dbcfg, cached=False)
    prices_df = prices_df[prices_df['Symbol'].isin(tickers)]

    missing = set(tickers) - set(prices_df['Symbol'])
    if missing:
        closes_df = mysql_to_df(read_latest_closing_prices_query,
                                read_latest_closing_prices_columns,
                                dbcfg, cached=False)
        closes_df = closes_df[closes_df['Symbol'].isin(missing)]
        # Defense in depth: read_latest_closing_prices_query already
        # collapses assets_history's (date, symbol, account_type) rows to
        # one per symbol via GROUP BY, but a symbol held across multiple
        # account types would otherwise come back duplicated here and
        # double its Current Value after the merge in
        # get_portfolio_current_value() -- dedupe defensively too.
        closes_df = closes_df.drop_duplicates(subset='Symbol', keep='first')
        prices_df = pd.concat([prices_df, closes_df], ignore_index=True)

        # Neither the snapshot nor assets_history has a price for these --
        # they're silently dropped from the returned frame. That doesn't NaN
        # the portfolio total (pandas .sum() skips NaN by default), but it
        # DOES understate it with no visible sign, so surface it loudly here.
        still_missing = missing - set(closes_df['Symbol'])
        if still_missing:
            logger.warning(
                "No current price or historical close found for %s; "
                "excluded from portfolio value.", sorted(still_missing))

    return prices_df.reset_index(drop=True)


def get_current_price(tickers: list) -> pd.DataFrame:
    """
    Given list of tickers, return current/realtime price data

    In read-only mode this reads the current_prices snapshot table rather than
    calling yfinance, so the web tier needs no network egress.

    Returns:
        current_prices_df: Symbol, Current Price
    """
    if PORTFOLIO_READ_ONLY:
        return read_current_prices_from_db(tickers)

    current_prices = _gen_current_prices(tickers)

    return pd.DataFrame(current_prices)
    
# For the ticker, unit and length specified, retrieve summary returns 
# For instance, if unit is "months" and length is 3, returns the closing price
# for each of the last 3 months
# Currently defaults to daily data
def get_summary_returns(tickers, unit="months", length=3, 
                        interval="daily", close=True, pct_change=True
                        ):
    # interval_map = {'daily': '1d', 'weekly': '1wk', 'monthly': '1mo',
    #                 'quarterly': '3mo', 'yearly': '1y'}
    start_date = get_dates_from_desc(unit, length)
    raw_price_data = \
        get_historical_prices(tickers, start=start_date, interval=interval)
                
    # TODO: Pull out pct_ change to external function                
    # Add percent change column to each dataframe
    if pct_change:
        data_type = "Close" if close else "Open"
        for symbol, data in raw_price_data.items():
            raw_pct_change = data[data_type].pct_change() * 100
            raw_price_data[symbol]['Percent Change'] = \
                round(raw_pct_change, 2)
        
    return raw_price_data