# Tax-Lot Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the FIFO tax-lot ledger `gen_hist_quantities` already computes and discards, so a caller can ask what lots a position is made of.

**Architecture:** `gen_hist_quantities` gains a keyword-only `return_lots` flag that hands back its existing `purchase_list` alongside the DataFrame it already returns. A new `libraries/tax_lots.py` is a thin read surface over that: it groups exactly as `gen_hist_quantities_mult` does, keeps open lots, derives the holding period, and refuses to return a lot set that does not account for every share a held position carries.

**Tech Stack:** Python, pandas, `unittest` (not pytest), MySQL via `libraries/db`.

**Spec:** `docs/superpowers/specs/2026-08-28-tax-lot-ledger-design.md`

## Global Constraints

- **One replay, not two.** Lots come from `gen_hist_quantities`' existing `purchase_list`. Do not write a second event-replay loop anywhere. The whole point is that lot-level and position-level figures cannot disagree.
- **`return_lots` defaults to `False` and is keyword-only.** Every existing caller must be byte-for-byte unaffected. `gen_hist_quantities`' three existing tests in `tests/libraries/test_helpers.py` must pass **unedited** — they are the regression guard.
- **Do not modify `gen_hist_quantities`' replay logic.** The only change to that function is the flag and the return. Splits, FIFO depletion, and both acquisition branches stay exactly as they are.
- **The `'Agnostic'` grouping rule must be copied exactly.** Splits and acquisitions carry `AccountType = 'Agnostic'`. Filter the *group list* to `['Discretionary', 'Retirement']`, but include `'Agnostic'` rows in *every* group's event slice. A naive `groupby(['Symbol', 'AccountType'])` drops every split and yields negative share counts.
- **Reconciliation is scoped to held positions** (aggregate quantity `> 0`). Positions at zero are excluded from the result entirely. Without this, `strict=True` raises on the first real call, because historical acquisitions and closed positions leave stale lots behind.
- **Open lots only** (`remaining_quantity > 0`). A depleted lot carries no derivable realized gain — every `sell` row has a null `PricePerShare`.
- **`as_of` affects holding-period arithmetic only.** It does not rewind the ledger. Lots are always the current open set.
- **This repository is public. No real portfolio figure, holding, or date in any tracked file.** Fixtures are synthetic round numbers.
- **Tests are `unittest`.** Run from the repo root with `./venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"`. The `-t .` matters: without it `tests.libraries` shadows `libraries/` and every module fails to import.

---

### Task 1: `return_lots` on the existing replay

**Files:**
- Modify: `libraries/helpers.py` (`gen_hist_quantities`, signature at `:154`, return at `:325`)
- Test: `tests/libraries/test_helpers.py` (append only)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `gen_hist_quantities(df, cadence='daily', expand_chronology=True, *, return_lots=False)`. With `return_lots=True` returns `(quantities_df, lots)` where `lots` is the raw `purchase_list`: a list of dicts with keys `Date`, `initial_quantity`, `remaining_quantity`, `purchase_price`. Prices are split-adjusted; the list is date-ordered.

- [ ] **Step 1: Write the failing tests**

Append to `tests/libraries/test_helpers.py`. Do not edit the three existing `gen_hist_quantities` tests — they are the regression guard.

```python
    def test_gen_hist_quantities_default_return_is_unchanged(self):
        """Every existing caller must still get a bare DataFrame."""
        result = gen_hist_quantities(self.test_data)
        self.assertIsInstance(result, pd.DataFrame)
        self.assertNotIsInstance(result, tuple)

    def test_gen_hist_quantities_returns_lots_when_asked(self):
        result, lots = gen_hist_quantities(self.test_data, return_lots=True)
        self.assertIsInstance(result, pd.DataFrame)
        self.assertIsInstance(lots, list)
        # setUp buys 100 @150 then 50 @155, then sells 75.
        # FIFO takes all 75 from the first lot, leaving 25 there and 50 intact.
        open_lots = [lot for lot in lots if lot['remaining_quantity'] > 0]
        self.assertEqual(len(open_lots), 2)
        self.assertAlmostEqual(open_lots[0]['remaining_quantity'], 25)
        self.assertAlmostEqual(open_lots[0]['purchase_price'], 150.0)
        self.assertAlmostEqual(open_lots[1]['remaining_quantity'], 50)
        self.assertAlmostEqual(open_lots[1]['purchase_price'], 155.0)

    def test_gen_hist_quantities_lots_are_split_adjusted(self):
        split_data = self.test_data.copy()
        split_data.loc[len(split_data)] = {
            'Date': '2024-01-04',
            'Symbol': 'AAPL',
            'Action': 'split',
            'Quantity': 0,
            'PricePerShare': 0,
            'Multiplier': 2,
        }
        _, lots = gen_hist_quantities(split_data, return_lots=True)
        open_lots = [lot for lot in lots if lot['remaining_quantity'] > 0]
        # A 2:1 split doubles each lot's shares and halves its price, so the
        # basis each lot carries is unchanged. Checking both sides catches a
        # rescale applied to only one of them.
        self.assertAlmostEqual(open_lots[0]['remaining_quantity'], 50)
        self.assertAlmostEqual(open_lots[0]['purchase_price'], 75.0)
        self.assertAlmostEqual(
            open_lots[0]['remaining_quantity'] * open_lots[0]['purchase_price'],
            25 * 150.0)

    def test_gen_hist_quantities_return_lots_is_keyword_only(self):
        # Positional would silently become expand_chronology's neighbour and
        # break the moment a parameter is inserted.
        with self.assertRaises(TypeError):
            gen_hist_quantities(self.test_data, 'daily', True, True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m unittest tests.libraries.test_helpers -v`
Expected: FAIL — `gen_hist_quantities() got an unexpected keyword argument 'return_lots'`

- [ ] **Step 3: Change the signature**

In `libraries/helpers.py`, replace the `gen_hist_quantities` signature:

```python
def gen_hist_quantities(asset_event_log_df: pd.DataFrame,
                        cadence: str='daily',
                        expand_chronology: bool=True,
                        *,
                        return_lots: bool=False):
```

The `-> pd.DataFrame` annotation is deliberately dropped: the return type now
depends on the flag, and an annotation that lies is worse than none.

Add to the docstring, after the existing `Returns:` block:

```
    If return_lots is True, returns (quantities_df, lots) instead, where lots
    is the internal purchase_list: one dict per purchase tranche, with keys
    Date, initial_quantity, remaining_quantity and purchase_price. Prices are
    split-adjusted. This is the same list the CostBasis above is derived from,
    which is the point - a second replay could drift from it.
```

- [ ] **Step 4: Change the return**

`libraries/helpers.py:325` is `gen_hist_quantities`' return. (Line 370 is
`gen_hist_quantities_mult`'s — leave that one alone.) Replace:

```python
    return quantities_df
```

with:

```python
    if return_lots:
        return quantities_df, purchase_list
    return quantities_df
```

- [ ] **Step 5: Run the tests**

Run: `./venv/bin/python -m unittest tests.libraries.test_helpers -v`
Expected: PASS, including the three pre-existing `gen_hist_quantities` tests unedited.

- [ ] **Step 6: Run the full suite**

Run: `./venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"`
Expected: `Ran 185 tests`, `OK` (181 baseline + 4).

- [ ] **Step 7: Commit**

```bash
git add libraries/helpers.py tests/libraries/test_helpers.py
git commit -m "feat(helpers): let gen_hist_quantities return its purchase lots"
```

---

### Task 2: `libraries/tax_lots.py` — the read surface

**Files:**
- Create: `libraries/tax_lots.py`
- Test: `tests/libraries/test_tax_lots.py`

**Interfaces:**
- Consumes: `gen_hist_quantities(df, expand_chronology=False, return_lots=True) -> (df, lots)` from Task 1; `build_master_log(symbols=[], account_type=None)` from `libraries/helpers.py:50`.
- Produces: `get_tax_lots(...)`, `LotReconciliationError`, `LOT_COLUMNS`.

**Two implementation notes that are not obvious:**

1. **Call the replay with `expand_chronology=False`.** The default reindexes to one row per calendar day from the first event to today, for every position. This needs only the final state, and the event-row form gives it in the last row far more cheaply.
2. **`_log=None` is a test seam.** `build_master_log` hits MySQL. The seam lets every behavioural test run offline against synthetic frames, which is also what keeps real holdings out of this public repo.

- [ ] **Step 1: Write the failing tests**

```python
# tests/libraries/test_tax_lots.py
import unittest
from unittest.mock import patch

import pandas as pd

from libraries import tax_lots
from libraries.tax_lots import LotReconciliationError, get_tax_lots


def _log(rows):
    """A master-log-shaped frame. All figures synthetic (public repo)."""
    return pd.DataFrame(rows, columns=['Date', 'Symbol', 'Action', 'Quantity',
                                       'PricePerShare', 'Multiplier',
                                       'AccountType'])


class TestGetTaxLots(unittest.TestCase):
    def setUp(self):
        self.simple = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
            ['2024-06-01', 'AAA', 'buy', 50, 20.0, 1, 'Discretionary'],
        ])

    def test_a_buy_becomes_one_lot(self):
        lots = get_tax_lots(_log=_log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
        ]), as_of='2024-12-31')
        self.assertEqual(len(lots), 1)
        row = lots.iloc[0]
        self.assertEqual(row['Symbol'], 'AAA')
        self.assertEqual(row['AccountType'], 'Discretionary')
        self.assertAlmostEqual(row['Quantity'], 100)
        self.assertAlmostEqual(row['CostPerShare'], 10.0)
        self.assertAlmostEqual(row['CostBasis'], 1000.0)

    def test_fifo_depletes_the_oldest_lot_first(self):
        rows = self.simple.copy()
        rows.loc[len(rows)] = ['2024-07-01', 'AAA', 'sell', 100, None, 1,
                               'Discretionary']
        lots = get_tax_lots(_log=rows, as_of='2024-12-31')
        # The 2024-01 lot is consumed entirely; only the 2024-06 lot survives.
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots.iloc[0]['Quantity'], 50)
        self.assertAlmostEqual(lots.iloc[0]['CostPerShare'], 20.0)

    def test_a_split_rescales_lots_without_changing_basis(self):
        rows = self.simple.copy()
        rows.loc[len(rows)] = ['2024-07-01', 'AAA', 'split', 0, 0, 2,
                               'Agnostic']
        lots = get_tax_lots(_log=rows, as_of='2024-12-31')
        self.assertAlmostEqual(lots['Quantity'].sum(), 300)
        self.assertAlmostEqual(lots['CostBasis'].sum(), 100 * 10.0 + 50 * 20.0)

    def test_agnostic_rows_reach_every_account(self):
        """A split tagged 'Agnostic' must apply to each account's replay.

        Dropping it silently omits the split, which is how a naive
        groupby(['Symbol','AccountType']) produces negative share counts.
        """
        rows = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
            ['2024-01-01', 'AAA', 'buy', 40, 10.0, 1, 'Retirement'],
            ['2024-07-01', 'AAA', 'split', 0, 0, 2, 'Agnostic'],
        ])
        lots = get_tax_lots(_log=rows, as_of='2024-12-31')
        by_account = lots.groupby('AccountType')['Quantity'].sum()
        self.assertAlmostEqual(by_account['Discretionary'], 200)
        self.assertAlmostEqual(by_account['Retirement'], 80)

    def test_accounts_are_never_merged(self):
        rows = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
            ['2024-01-01', 'AAA', 'buy', 40, 25.0, 1, 'Retirement'],
        ])
        lots = get_tax_lots(_log=rows, as_of='2024-12-31')
        self.assertEqual(sorted(lots['AccountType'].unique()),
                         ['Discretionary', 'Retirement'])
        self.assertEqual(len(lots), 2)

    def test_long_term_flips_at_the_boundary(self):
        rows = _log([['2024-01-01', 'AAA', 'buy', 10, 5.0, 1, 'Discretionary']])
        short = get_tax_lots(_log=rows, as_of='2024-12-31')   # 365 days
        long_ = get_tax_lots(_log=rows, as_of='2025-01-01')   # 366 days
        self.assertEqual(short.iloc[0]['DaysHeld'], 365)
        self.assertFalse(bool(short.iloc[0]['LongTerm']))
        self.assertEqual(long_.iloc[0]['DaysHeld'], 366)
        self.assertTrue(bool(long_.iloc[0]['LongTerm']))

    def test_a_fully_sold_position_is_absent(self):
        rows = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
            ['2024-07-01', 'AAA', 'sell', 100, None, 1, 'Discretionary'],
        ])
        lots = get_tax_lots(_log=rows, as_of='2024-12-31')
        self.assertTrue(lots.empty)
        self.assertEqual(list(lots.columns), tax_lots.LOT_COLUMNS)

    def test_symbol_and_account_filters(self):
        rows = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
            ['2024-01-01', 'BBB', 'buy', 10, 30.0, 1, 'Discretionary'],
            ['2024-01-01', 'AAA', 'buy', 5, 10.0, 1, 'Retirement'],
        ])
        self.assertEqual(set(get_tax_lots(_log=rows, symbol='AAA',
                                          as_of='2024-12-31')['Symbol']), {'AAA'})
        self.assertEqual(set(get_tax_lots(_log=rows, account_type='Retirement',
                                          as_of='2024-12-31')['AccountType']),
                         {'Retirement'})

    def test_unknown_symbol_returns_an_empty_frame_not_an_error(self):
        lots = get_tax_lots(_log=self.simple, symbol='ZZZ', as_of='2024-12-31')
        self.assertTrue(lots.empty)
        self.assertEqual(list(lots.columns), tax_lots.LOT_COLUMNS)


class TestReconciliation(unittest.TestCase):
    """The guard is tested against the replay's contract, not through it.

    Producing a genuine mismatch requires an acquisition, which needs the
    database; patching the replay tests the guard itself.
    """

    def setUp(self):
        self.rows = _log([
            ['2024-01-01', 'AAA', 'buy', 100, 10.0, 1, 'Discretionary'],
        ])
        self.quantities = pd.DataFrame({'Quantity': [100.0]})
        self.short_lots = [{'Date': pd.Timestamp('2024-01-01'),
                            'initial_quantity': 60.0,
                            'remaining_quantity': 60.0,
                            'purchase_price': 10.0}]

    @patch('libraries.tax_lots.gen_hist_quantities')
    def test_strict_raises_when_lots_do_not_cover_the_position(self, replay):
        replay.return_value = (self.quantities, self.short_lots)
        with self.assertRaises(LotReconciliationError) as ctx:
            get_tax_lots(_log=self.rows, as_of='2024-12-31')
        self.assertIn('AAA', str(ctx.exception))

    @patch('libraries.tax_lots.gen_hist_quantities')
    def test_non_strict_returns_the_lots_and_warns(self, replay):
        replay.return_value = (self.quantities, self.short_lots)
        with self.assertWarns(UserWarning):
            lots = get_tax_lots(_log=self.rows, as_of='2024-12-31', strict=False)
        self.assertAlmostEqual(lots['Quantity'].sum(), 60.0)

    @patch('libraries.tax_lots.gen_hist_quantities')
    def test_a_zero_quantity_position_with_stale_lots_is_skipped(self, replay):
        """Historical acquisitions and closed positions leave lots behind.

        They are not held, so they have no open lots to report and must not
        trip the guard - otherwise strict=True raises on the first real call.
        """
        replay.return_value = (pd.DataFrame({'Quantity': [0.0]}),
                               self.short_lots)
        lots = get_tax_lots(_log=self.rows, as_of='2024-12-31')
        self.assertTrue(lots.empty)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m unittest tests.libraries.test_tax_lots -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libraries.tax_lots'`

- [ ] **Step 3: Write the module**

```python
# libraries/tax_lots.py
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

# Splits and acquisitions are tagged 'Agnostic' rather than a real account.
REAL_ACCOUNT_TYPES = ['Discretionary', 'Retirement']
AGNOSTIC = 'Agnostic'

# US long-term capital gains boundary: held more than one year.
LONG_TERM_DAYS = 365

_QUANTITY_TOLERANCE = 0.01


class LotReconciliationError(RuntimeError):
    """Open lots do not account for every share a held position carries."""


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=LOT_COLUMNS)


def get_tax_lots(symbol: str=None, account_type: str=None, as_of=None,
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
    if log is None or log.empty:
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
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m unittest tests.libraries.test_tax_lots -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"`
Expected: `Ran 197 tests`, `OK` (185 + 12).

- [ ] **Step 6: Verify against the live ledger**

This is the check no synthetic fixture gives: that the guard is silent on real
data, and that lots sum to the position for everything actually held.

```bash
./venv/bin/python -c "
from libraries.tax_lots import get_tax_lots
lots = get_tax_lots()
print('positions with open lots:', lots.groupby(['Symbol','AccountType']).ngroups)
print('total open lots         :', len(lots))
print('long-term share of lots :', round(100*lots['LongTerm'].mean()), '%')
print('any non-positive lot    :', bool((lots['Quantity'] <= 0).any()))
print('any null field          :', bool(lots.isna().any().any()))
"
```

Expected: it completes without raising (strict=True is the default, so a
reconciliation failure on any held position would surface here), reports a
plausible number of positions, and shows no non-positive quantities and no
nulls. **Do not paste the output into any tracked file** — this repository is
public.

- [ ] **Step 7: Commit**

```bash
git add libraries/tax_lots.py tests/libraries/test_tax_lots.py
git commit -m "feat(tax_lots): expose open tax lots per symbol and account"
```

---

## Notes for the executor

- Test totals assume a 181 baseline. If yours differs, assert the delta rather than the total. If the suite errors with `Access denied for user`, the DB credential in `libraries/db/pwd.py` is stale — that is environment, not code.
- No task may add a second event-replay loop. If one seems necessary, stop and report.
- No task may put a real portfolio figure, holding or date in a tracked file.
