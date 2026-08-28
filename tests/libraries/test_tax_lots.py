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
