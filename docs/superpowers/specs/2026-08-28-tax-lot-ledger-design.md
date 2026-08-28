# Tax-Lot Ledger — Design

**Date:** 2026-08-28
**Status:** Design — pending user review
**Project root:** `~/code/python/wake`

## 1. Purpose

Wake already computes a tax-lot ledger on every history run, and throws it away.

`gen_hist_quantities` (`libraries/helpers.py:154`) replays a single asset's
event log and maintains `purchase_list` (`:196`) — one entry per purchase
tranche holding its date, initial quantity, remaining quantity and price per
share. Buys append to it (`:225`), sells deplete it oldest-first, and splits
rescale every entry's quantity and price. It is the source of the `CostBasis`
the dashboard has displayed for years. The function returns
`Date, Symbol, Quantity, CostBasis, AccountType` and the lots go out of scope.

This exposes them. It is the prerequisite for answering questions of the form
"what should I sell to raise $X, and what would it cost me in tax" — which
cannot be answered from a position-level cost basis, because the tax on a sale
depends on which lots it comes from.

### Success criteria
- Open lots are retrievable per symbol and account, with acquisition date,
  quantity, split-adjusted cost per share and holding period.
- The lots and the dashboard's cost basis are computed by the same replay and
  cannot disagree.
- A lot set that does not account for every share held is loud, not silent.
- No existing caller of `gen_hist_quantities` changes behaviour.

### Non-goals
Realized gains, current prices, unrealized P&L, tax estimation, UI, and any
change to how history is generated. This is a read surface over an existing
computation.

## 2. What is already true

Verified against the live event log at design time:

| Fact | Status |
|---|---|
| A per-tranche lot list exists in `gen_hist_quantities` | yes, `purchase_list` |
| Splits rescale each lot (quantity ×m, price ÷m) | yes |
| Sells deplete oldest-first (FIFO) | yes |
| Lots are grouped per `(Symbol, AccountType)` | yes, via `gen_hist_quantities_mult:327` |
| Dividends excluded from the replay | yes, `NON_QUANTITY_ASSET_EVENTS` (`libraries/globals.py:4`) |
| Lots survive the function return | **no** |

Three constraints follow from the data, and each shapes the design:

- **Realized gains are not derivable.** Every `sell` row has a null
  `PricePerShare`; only buys carry prices. The ledger can say what is held and
  what it cost, never what a past sale netted. This does not block the
  liquidation planner, which needs open lots only.
- **Account type is already in the grain, and it is a tax boundary.** A sale in
  a retirement account has no tax consequence; one in a taxable account does.
  Any consumer must not merge them, so the ledger never aggregates across
  accounts.
- **FIFO matches the brokerage.** The user confirmed their broker's default is
  FIFO, so Wake's accounting and the real depletion order agree. A plan built
  on these lots describes what would actually happen. Were that to change, this
  assumption would need revisiting — it is recorded here for that reason.

## 3. Architecture

### 3.1 `return_lots` on the existing replay

`gen_hist_quantities` gains a keyword-only parameter:

```python
def gen_hist_quantities(asset_event_log_df, cadence='daily',
                        expand_chronology=True, *, return_lots=False):
```

Default `False` returns exactly what it returns today, so every existing caller
is unchanged. `True` returns `(quantities_df, lots)` where `lots` is the final
`purchase_list`.

**This parameter is the whole design.** The alternative — a second module that
replays the event log independently — would be easier to read and strictly
worse: two implementations of split handling, FIFO depletion and acquisition
conversion, free to drift apart, with the lot-level and position-level figures
disagreeing about the same money. Sharing the replay makes that disagreement
impossible by construction rather than by discipline.

### 3.2 `libraries/tax_lots.py` (new) — the read surface

A new module rather than more of `libraries/helpers.py`, which is already 729
lines and owns a different concern (history generation). This is a small,
focused view with its own tests.

```python
get_tax_lots(symbol=None, account_type=None, as_of=None,
             strict=True) -> pd.DataFrame
#  Symbol  AccountType  AcquiredDate  Quantity
#  CostPerShare  CostBasis  DaysHeld  LongTerm
```

It builds the master log, groups by `(Symbol, AccountType)` exactly as
`gen_hist_quantities_mult` does, calls the replay with `return_lots=True`,
keeps lots with `remaining_quantity > 0`, and derives the holding period.

**The grouping rule is not obvious and must be copied exactly.** Splits and
acquisitions carry `AccountType = 'Agnostic'`, not a real account.
`gen_hist_quantities_mult` (`libraries/helpers.py:327`) therefore filters the
*group list* to `['Discretionary', 'Retirement']` while including
`'Agnostic'` rows in *every* group's event slice:

```python
symbols = symbols[symbols['AccountType'].isin(['Discretionary', 'Retirement'])]
...
(df['Symbol'] == symbol) & ((df['AccountType'] == account_type) |
                            (df['AccountType'] == 'Agnostic'))
```

A naive `groupby(['Symbol', 'AccountType'])` silently drops every split from
the trade groups. Measured on the live log, that produces negative share
counts on several positions, because years of split multipliers are never
applied. The lots would be wrong in a way that looks like data corruption
rather than a grouping bug.

**Open lots only.** A depleted lot carries no derivable realized gain (see §2),
so returning closed lots would offer a column that could never be filled.

**`as_of` defaults to today but is a parameter.** `DaysHeld` and `LongTerm`
otherwise change meaning every night, which makes them untestable. `LongTerm`
is `DaysHeld > 365`, the US long-term capital gains boundary.

**`as_of` affects the holding-period arithmetic only; it does not rewind the
ledger.** Lots are always the current open set. Reconstructing the lot ledger
as it stood on a past date is a different feature — it would need the replay
truncated at that date — and is a non-goal here. The parameter exists so tests
are deterministic, and the docstring says so plainly, because the name invites
the other reading.

**`CostPerShare` is split-adjusted**, because `purchase_list` already divides it
at each split. `CostBasis` is `Quantity × CostPerShare` for the remaining
shares, not the original purchase.

### 3.3 The reconciliation guard

For each `(Symbol, AccountType)`, the summed lot quantity must equal the
aggregate `Quantity` from the same replay. Both numbers come from one call, so
the check is free.

**Only positions actually held are reconciled**, meaning aggregate quantity
greater than zero. A position at zero has no open lots to report, and
reporting none is the correct answer regardless of what the replay's lot list
still contains.

That qualifier is load-bearing, not a convenience. Measured on the live log,
a small number of `(symbol, account)` pairs fail reconciliation, and **none is
currently held**: every one is either a side of a historical acquisition or a
closed position, where the replay leaves stale lots behind (§3.4).
Without the qualifier, `strict=True` would raise on the very first call
against real data, and the guard would be turned off within a day. With it,
the guard is silent today and fires only when a position someone actually
holds fails to account for its shares — which is the case that matters.

`LotReconciliationError` is defined in `libraries/tax_lots.py`. With
`strict=True` (the default) a mismatch raises it, naming the symbol, account,
expected and actual quantities. With `strict=False` the lots are returned
anyway so the discrepancy can be inspected.

The guard exists because of a known gap, below. Its value is general: a ledger
that quietly accounts for fewer shares than are held would make a future sell
plan confidently wrong — under-planning a raise, or omitting a lot from a tax
calculation — with nothing to signal it. Defaulting to loud is the right
trade for money.

### 3.4 The acquisition gap — detected, not fixed

The `acquisition-acquirer` branch (`libraries/helpers.py:261`) updates
`total_quantity` and `cost_basis` from the acquired position but never appends
to `purchase_list`. Shares received through an acquisition therefore have no
lot, and `get_tax_lots` would under-account for such a position.

**Not fixed here, deliberately.** Appending a lot there would change no current
output, since quantity and cost basis are maintained separately — but it would
change behaviour on the *next* path: a later `sell` currently depletes other
lots, so historical cost basis for an acquired-then-sold position may already
be affected. That is a figure the dashboard has displayed for years, and
correcting it deserves its own change with its own verification rather than
riding along on a read surface.

The acquisitions in the log involve no currently-held position — every
acquirer was subsequently sold — so the gap is inert today. The guard in §3.3 makes
it impossible to miss if that changes.

## 4. Error handling

- **Reconciliation mismatch** → `LotReconciliationError` under `strict=True`;
  lots returned with the discrepancy visible under `strict=False`.
- **Unknown symbol or account** → empty DataFrame with the full column set, not
  an error. Asking about something not held is a legitimate question with an
  empty answer.
- **A position with no open lots** (fully sold) → absent from the result, and
  its aggregate quantity is zero, so reconciliation passes.
- **The replay itself raising** is left to propagate. `gen_hist_quantities` is
  existing, exercised code; wrapping its failures here would hide them.

## 5. Testing

`unittest`, in `tests/libraries/test_tax_lots.py`, following the
`setUp()`-with-sample-DataFrames pattern of `tests/libraries/test_helpers.py`.
Run with `python -m unittest discover -s tests -t . -p "test_*.py"` from the
repo root (the `-t .` matters — without it `tests.libraries` shadows
`libraries/`).

- A single buy yields one lot with matching date, quantity and price.
- Two buys then a partial sell: FIFO leaves the older lot depleted first and
  the newer intact, and the remaining quantities are right.
- A split multiplies each lot's quantity and divides its price, leaving
  `CostBasis` unchanged — the invariant that catches a rescale applied to only
  one of the two.
- `LongTerm` is `False` at 365 days and `True` at 366, against a fixed `as_of`.
- Fully depleted lots are absent.
- Reconciliation passes on ordinary data, and raises on a synthesized
  acquisition case under `strict=True` while returning rows under
  `strict=False`.
- `gen_hist_quantities`' three existing tests pass **unedited** — the guard
  that `return_lots` changed nothing for existing callers.

**Fixtures are synthetic.** This repository is public; no real portfolio
figure, holding or date appears in a tracked file. Round illustrative numbers
exercise every branch identically.

## 6. Where this sits

Second of four pieces identified while designing position P&L in the sibling
Vantage project:

1. **Position P&L** — shipped in Vantage.
2. **Lot-level accounting** — this document.
3. **Valuations** — fundamentals in Vantage's market data.
4. **Liquidation planner** — "what should I sell to raise $X". Consumes this
   ledger plus user constraints (liquidity need, risk budget, tax bracket).
   The objective function belongs in Vantage beside `interests.yaml`; the
   arithmetic is deterministic Python that shows its work, never a model's
   mental arithmetic.

## 7. Recorded, not addressed here

**Wake's query cache can mask a database outage.** With
`MYSQL_CACHE_ENABLED = True` (`libraries/globals.py:48`), a portfolio read
succeeded and reported itself available while direct connections were failing
with an authentication error. Vantage's adapter has a real degradation path —
it reports `available=False` with a note — but the cache answered first, so the
failure was invisible and a weekly brief would have been written against stale
holdings without saying so. Out of scope for a read surface over the replay;
worth its own change, and it would sit underneath anything built on this
ledger.
