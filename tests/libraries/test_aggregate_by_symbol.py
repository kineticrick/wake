import datetime
import unittest

import pandas as pd

from libraries.helpers import aggregate_assets_history_by_symbol


def _reference_impl(df):
    """The original row-wise implementation, kept as the equivalence oracle."""
    if df.empty:
        return df
    out = df.groupby(['Date', 'Symbol'], as_index=False).agg(
        Quantity=('Quantity', 'sum'),
        CostBasis=('CostBasis', 'sum'),
        ClosingPrice=('ClosingPrice', 'first'),
        Value=('Value', 'sum'),
    )
    out['PercentReturn'] = out.apply(
        lambda r: (r['Value'] - r['CostBasis']) / r['CostBasis'] * 100
        if r['CostBasis'] else 0.0, axis=1)
    cols = ['CostBasis', 'ClosingPrice', 'Value', 'PercentReturn']
    out[cols] = out[cols].round(2)
    return out


class TestAggregateAssetsHistoryBySymbol(unittest.TestCase):

    def setUp(self):
        d1 = datetime.date(2026, 1, 5)
        d2 = datetime.date(2026, 1, 6)
        # QQQ is held in TWO accounts on the same date (the multi-account case),
        # ZERO has a zero cost basis (the division-guard case),
        # EXIT went to quantity 0 (the exited-asset case).
        self.df = pd.DataFrame([
            {'Date': d1, 'Symbol': 'QQQ',  'Quantity': 10.0, 'CostBasis': 1000.0,
             'ClosingPrice': 150.0, 'Value': 1500.0},
            {'Date': d1, 'Symbol': 'QQQ',  'Quantity': 5.0,  'CostBasis': 400.0,
             'ClosingPrice': 150.0, 'Value': 750.0},
            {'Date': d1, 'Symbol': 'ZERO', 'Quantity': 3.0,  'CostBasis': 0.0,
             'ClosingPrice': 20.0,  'Value': 60.0},
            {'Date': d2, 'Symbol': 'EXIT', 'Quantity': 0.0,  'CostBasis': 0.0,
             'ClosingPrice': 12.0,  'Value': 0.0},
            {'Date': d2, 'Symbol': 'QQQ',  'Quantity': 15.0, 'CostBasis': 1400.0,
             'ClosingPrice': 151.0, 'Value': 2265.0},
        ])

    def test_matches_reference_implementation(self):
        expected = _reference_impl(self.df.copy())
        actual = aggregate_assets_history_by_symbol(self.df.copy())
        pd.testing.assert_frame_equal(actual, expected)

    def test_zero_cost_basis_yields_zero_return_not_inf(self):
        out = aggregate_assets_history_by_symbol(self.df.copy())
        zero_row = out[(out['Symbol'] == 'ZERO')].iloc[0]
        self.assertEqual(zero_row['PercentReturn'], 0.0)

    def test_multi_account_rows_are_summed(self):
        out = aggregate_assets_history_by_symbol(self.df.copy())
        d1 = datetime.date(2026, 1, 5)
        qqq = out[(out['Symbol'] == 'QQQ') & (out['Date'] == d1)].iloc[0]
        self.assertEqual(qqq['Quantity'], 15.0)
        self.assertEqual(qqq['CostBasis'], 1400.0)
        self.assertEqual(qqq['Value'], 2250.0)

    def test_empty_frame_passes_through(self):
        empty = pd.DataFrame(
            columns=['Date', 'Symbol', 'Quantity', 'CostBasis',
                     'ClosingPrice', 'Value'])
        out = aggregate_assets_history_by_symbol(empty)
        self.assertTrue(out.empty)


if __name__ == '__main__':
    unittest.main()
