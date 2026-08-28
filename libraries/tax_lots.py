"""Tax-lot read surface over the history replay.

gen_hist_quantities already maintains a FIFO lot ledger while computing
CostBasis, and discards it at the return. This exposes it. The lots and the
position-level cost basis therefore come from one replay and cannot disagree -
a second implementation of split handling and FIFO depletion would be easier
to read and free to drift, which is the whole reason it is not done that way.

Open lots only: every 'sell' row has a null PricePerShare, so a depleted lot
carries no derivable realized gain.
"""
import warnings

import pandas as pd

from libraries.helpers import build_master_log, gen_hist_quantities

LOT_COLUMNS = ['Symbol', 'AccountType', 'AcquiredDate', 'Quantity',
               'CostPerShare', 'CostBasis', 'DaysHeld', 'LongTerm']

# Matches the dtypes a populated result actually carries, so an empty result
# is the same shape as a populated one rather than an all-object frame a
# consumer would need to special-case before doing dtype-sensitive work
# (datetime arithmetic, numeric comparisons) on it.
LOT_DTYPES = {
    'Symbol': str,
    'AccountType': str,
    'AcquiredDate': 'datetime64[us]',
    'Quantity': 'float64',
    'CostPerShare': 'float64',
    'CostBasis': 'float64',
    'DaysHeld': 'int64',
    'LongTerm': 'bool',
}

# Splits and acquisitions are tagged 'Agnostic' rather than a real account.
REAL_ACCOUNT_TYPES = ['Discretionary', 'Retirement']
AGNOSTIC = 'Agnostic'

# US long-term capital gains boundary: held more than one year.
LONG_TERM_DAYS = 365

_QUANTITY_TOLERANCE = 0.01


class LotReconciliationError(RuntimeError):
    """Open lots do not account for every share a held position carries."""


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        {col: pd.Series(dtype=dtype) for col, dtype in LOT_DTYPES.items()},
        columns=LOT_COLUMNS)


def get_tax_lots(symbol: str=None, account_type: str=None, *, as_of=None,
                 strict: bool=True, _log: pd.DataFrame=None) -> pd.DataFrame:
    """Open tax lots per (symbol, account).

    as_of affects the holding-period arithmetic only - it does not rewind the
    ledger. Lots are always the current open set; reconstructing them as of a
    past date would need the replay truncated there, and is not this function.

    With strict=True a position whose lots do not sum to its quantity raises
    LotReconciliationError. With strict=False it warns and returns the lots
    anyway so the discrepancy can be inspected.

    _log injects a master log for testing; production reads it from the DB.
    """
    log = _log if _log is not None else build_master_log(
        symbols=[symbol] if symbol else [])
    # build_master_log always returns a DataFrame (mysql_to_df concats query
    # results into one even when a query returns zero rows), so it can never
    # be None here - only empty. No `log is None` check: that branch would be
    # untestable dead code.
    if log.empty:
        return _empty()

    as_of_ts = (pd.Timestamp(as_of) if as_of is not None
                else pd.Timestamp.today().normalize())

    pairs = log[['Symbol', 'AccountType']].drop_duplicates()
    pairs = pairs[pairs['AccountType'].isin(REAL_ACCOUNT_TYPES)]
    if symbol:
        pairs = pairs[pairs['Symbol'] == symbol]
    if account_type:
        pairs = pairs[pairs['AccountType'] == account_type]

    rows = []
    for _, pair in pairs.iterrows():
        sym, acct = pair['Symbol'], pair['AccountType']
        # 'Agnostic' rows are splits and acquisitions: they belong to every
        # account's replay for this symbol. Excluding them drops every split,
        # which yields negative share counts rather than an obvious error.
        events = log[(log['Symbol'] == sym) &
                     ((log['AccountType'] == acct) |
                      (log['AccountType'] == AGNOSTIC))]
        # expand_chronology=False: only the final state is needed, and the
        # default reindexes to one row per calendar day per position.
        quantities, lots = gen_hist_quantities(events,
                                               expand_chronology=False,
                                               return_lots=True)
        held = float(quantities['Quantity'].iloc[-1])
        if held <= 0:
            # Not held: no open lots to report. Historical acquisitions and
            # closed positions leave stale lots behind, so this also keeps
            # them from tripping the guard below.
            continue

        open_lots = [lot for lot in lots if lot['remaining_quantity'] > 0]
        lot_total = sum(lot['remaining_quantity'] for lot in open_lots)
        if abs(lot_total - held) > _QUANTITY_TOLERANCE:
            message = (f"{sym} ({acct}): open lots account for {lot_total:g} "
                       f"shares but the position holds {held:g}")
            if strict:
                raise LotReconciliationError(message)
            warnings.warn(message, UserWarning, stacklevel=2)

        for lot in open_lots:
            acquired = pd.Timestamp(lot['Date'])
            if acquired > as_of_ts:
                # as_of doesn't rewind the ledger (see docstring), so a past
                # as_of paired with a lot acquired since then produces a
                # negative DaysHeld that looks plausible rather than wrong.
                # Warn rather than raise: it's a documented hazard, not an
                # error, and the lots are still the correct current set.
                warnings.warn(
                    f"{sym} ({acct}): lot acquired {acquired.date()} is after "
                    f"as_of {as_of_ts.date()}; DaysHeld will be negative",
                    UserWarning, stacklevel=2)
            quantity = float(lot['remaining_quantity'])
            price = float(lot['purchase_price'])
            days_held = (as_of_ts - acquired).days
            rows.append({
                'Symbol': sym,
                'AccountType': acct,
                'AcquiredDate': acquired,
                'Quantity': quantity,
                'CostPerShare': price,
                'CostBasis': quantity * price,
                'DaysHeld': days_held,
                'LongTerm': days_held > LONG_TERM_DAYS,
            })

    if not rows:
        return _empty()
    return (pd.DataFrame(rows, columns=LOT_COLUMNS)
            .sort_values(['Symbol', 'AccountType', 'AcquiredDate'])
            .reset_index(drop=True))
