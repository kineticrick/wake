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

    def test_as_of_before_acquisition_warns_but_still_returns_the_lots(self):
        # as_of doesn't rewind the ledger (documented), so a past as_of paired
        # with a lot acquired since then yields a plausible-looking negative
        # DaysHeld rather than an error. The warning converts that from a
        # silent hazard into a detected one, without changing the return
        # value - the deterministic-test use case (as_of pinned in the past)
        # must still work, just noisily when it's genuinely misapplied.
        rows = _log([['2024-06-01', 'AAA', 'buy', 10, 5.0, 1, 'Discretionary']])
        with self.assertWarns(UserWarning):
            lots = get_tax_lots(_log=rows, as_of='2024-01-01')
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots.iloc[0]['Quantity'], 10)
        self.assertLess(lots.iloc[0]['DaysHeld'], 0)

    def test_a_fully_sold_position_is_absent(self):
        # Note: this cannot localize a break to a specific guard. The
        # `held <= 0` skip and the `remaining_quantity > 0` open-lots filter
        # are independent, and either one alone produces the empty result
        # asserted here - so this test survives either guard being removed
        # on its own. Not vacuous, just weaker than its name suggests.
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

    def test_as_of_strict_and_log_are_keyword_only(self):
        # Positional would silently miscount onto the wrong parameter -
        # get_tax_lots('AAA', 'Discretionary', None, False) would switch off
        # the reconciliation guard without raising anything.
        with self.assertRaises(TypeError):
            get_tax_lots('AAA', 'Discretionary', '2024-12-31', False, self.simple)

    def test_unknown_symbol_returns_an_empty_frame_not_an_error(self):
        lots = get_tax_lots(_log=self.simple, symbol='ZZZ', as_of='2024-12-31')
        self.assertTrue(lots.empty)
        self.assertEqual(list(lots.columns), tax_lots.LOT_COLUMNS)

    def test_an_empty_master_log_returns_an_empty_frame_not_an_error(self):
        """The guard's own condition, actually exercised.

        A zero-row-but-columned frame (`_log([])`) already falls through
        harmlessly via the `if not rows` branch further down, without ever
        needing the guard - it does not prove the guard does anything. A
        genuinely empty frame - no columns, matching what `log.empty` alone
        checks for - is the case the guard exists for: log[['Symbol',
        'AccountType']] on it raises KeyError once the guard is removed.
        """
        lots = get_tax_lots(_log=pd.DataFrame(), symbol='ZZZ', as_of='2024-12-31')
        self.assertTrue(lots.empty)
        self.assertEqual(list(lots.columns), tax_lots.LOT_COLUMNS)

    def test_empty_result_has_the_same_dtypes_as_a_populated_one(self):
        # A consumer that branches on dtype, or does datetime arithmetic on
        # AcquiredDate, should see one schema regardless of whether any lots
        # came back - not an all-object frame in the empty case.
        empty = get_tax_lots(_log=pd.DataFrame(), symbol='ZZZ', as_of='2024-12-31')
        populated = get_tax_lots(_log=self.simple, as_of='2024-12-31')
        self.assertTrue(empty.empty)
        self.assertFalse(populated.empty)
        for col in tax_lots.LOT_COLUMNS:
            self.assertEqual(empty[col].dtype, populated[col].dtype,
                             f"{col} dtype mismatch")


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
    def test_replay_is_called_with_expand_chronology_false(self, replay):
        # expand_chronology=False is a real dependency, not an incidental
        # default: with the default True, the replay reindexes to one row
        # per calendar day per position - a large silent cost across many
        # positions - even though only the final state (iloc[-1]) is used.
        # return_lots=True is what makes the lot ledger available at all.
        replay.return_value = (self.quantities, self.short_lots)
        with self.assertWarns(UserWarning):
            get_tax_lots(_log=self.rows, as_of='2024-12-31', strict=False)
        _, kwargs = replay.call_args
        self.assertEqual(kwargs.get('expand_chronology'), False)
        self.assertEqual(kwargs.get('return_lots'), True)

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
