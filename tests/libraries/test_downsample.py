import datetime
import unittest

import pandas as pd

from libraries.downsample import downsample_history, top_n_symbols


class TestDownsampleHistory(unittest.TestCase):

    def setUp(self):
        self.today = datetime.date(2026, 8, 24)
        # Three years of daily data for two symbols.
        dates = pd.date_range(end=pd.Timestamp(self.today),
                              periods=365 * 3, freq='D').date
        rows = []
        for symbol, base in (('AAA', 100.0), ('BBB', 50.0)):
            for i, d in enumerate(dates):
                rows.append({'Date': d, 'Symbol': symbol, 'Value': base + i})
        self.df = pd.DataFrame(rows)

    def test_output_stays_within_the_budget(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        for symbol in ('AAA', 'BBB'):
            kept = out[out['Symbol'] == symbol]
            self.assertLessEqual(len(kept), 100)

    def test_thinning_is_evenly_spaced_not_front_loaded(self):
        # A stride-based thin must sample across the whole range. A naive
        # head(budget) would pass a length check while showing only the
        # oldest slice of history.
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        kept = out[out['Symbol'] == 'AAA'].sort_values('Date')
        original = self.df[self.df['Symbol'] == 'AAA']
        span_kept = (kept['Date'].max() - kept['Date'].min()).days
        span_orig = (original['Date'].max() - original['Date'].min()).days
        self.assertEqual(span_kept, span_orig)

    def test_series_longer_than_budget_is_actually_thinned(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        self.assertLess(len(out), len(self.df))

    def test_first_and_last_point_per_group_are_preserved(self):
        out = downsample_history(self.df, group_cols=('Symbol',))
        for symbol in ('AAA', 'BBB'):
            original = self.df[self.df['Symbol'] == symbol]
            kept = out[out['Symbol'] == symbol]
            self.assertEqual(kept['Date'].min(), original['Date'].min())
            self.assertEqual(kept['Date'].max(), original['Date'].max())
            self.assertEqual(
                kept.loc[kept['Date'].idxmin(), 'Value'],
                original.loc[original['Date'].idxmin(), 'Value'])
            self.assertEqual(
                kept.loc[kept['Date'].idxmax(), 'Value'],
                original.loc[original['Date'].idxmax(), 'Value'])

    def test_short_history_is_returned_unchanged(self):
        recent = self.df[self.df['Date'] >=
                         self.today - datetime.timedelta(days=30)]
        out = downsample_history(recent, group_cols=('Symbol',),
                                 max_points_per_series=1000)
        self.assertEqual(len(out), len(recent))

    def test_empty_frame_passes_through(self):
        empty = pd.DataFrame(columns=['Date', 'Symbol', 'Value'])
        self.assertTrue(downsample_history(empty, group_cols=('Symbol',)).empty)

    def test_budget_of_one_still_preserves_both_endpoints(self):
        # A budget of 1 cannot literally be honored while keeping both
        # endpoints (that needs at least 2 points), so it must be clamped
        # rather than silently dropping the first point.
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=1)
        for symbol in ('AAA', 'BBB'):
            original = self.df[self.df['Symbol'] == symbol]
            kept = out[out['Symbol'] == symbol]
            self.assertEqual(kept['Date'].min(), original['Date'].min())
            self.assertEqual(kept['Date'].max(), original['Date'].max())


class TestDownsampleHistoryMultiGroupCols(unittest.TestCase):
    """Covers the GAP 1 fix: a chart trace can be identified by MORE than one
    column (Assets tab traces are (Symbol, AccountType) pairs). Grouping on
    only one of the two would let one account's endpoints leak into, or
    replace, another account's."""

    def setUp(self):
        self.today = datetime.date(2026, 8, 24)
        dates = pd.date_range(end=pd.Timestamp(self.today),
                              periods=365 * 2, freq='D').date
        rows = []
        # Same symbol, two accounts, with DIFFERENT value ranges so a mixup
        # between the two would be detectable.
        for account, base in (('Discretionary', 100.0), ('Retirement', 900.0)):
            for i, d in enumerate(dates):
                rows.append({'Date': d, 'Symbol': 'QQQ', 'AccountType': account,
                             'Value': base + i})
        self.df = pd.DataFrame(rows)

    def test_trace_count_is_unchanged(self):
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'))
        pairs_in = self.df[['Symbol', 'AccountType']].drop_duplicates()
        pairs_out = out[['Symbol', 'AccountType']].drop_duplicates()
        self.assertEqual(len(pairs_out), len(pairs_in))

    def test_endpoints_do_not_cross_accounts(self):
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'))
        for account in ('Discretionary', 'Retirement'):
            original = self.df[self.df['AccountType'] == account]
            kept = out[out['AccountType'] == account]
            self.assertEqual(kept['Date'].min(), original['Date'].min())
            self.assertEqual(kept['Date'].max(), original['Date'].max())
            self.assertEqual(
                kept.loc[kept['Date'].idxmin(), 'Value'],
                original.loc[original['Date'].idxmin(), 'Value'])
            self.assertEqual(
                kept.loc[kept['Date'].idxmax(), 'Value'],
                original.loc[original['Date'].idxmax(), 'Value'])

    def test_old_data_is_thinned_per_account(self):
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'))
        self.assertLess(len(out), len(self.df))


class TestDownsampleHistoryNonUniqueIndex(unittest.TestCase):
    """
    MINOR 4 regression: downsample_history dedupes on index LABELS (via
    `.loc[groupby(...).idxmin()]`), not rows. A caller that hands it a frame
    with a non-unique index (e.g. two per-symbol frames concatenated with
    `pd.concat([...], ignore_index=False)`, each independently indexed
    0..N-1) can lose a whole group with no error: idxmin()/idxmax() return
    labels, and `.loc[[label, ...]]` on a frame with duplicate labels fans
    out to every row sharing that label instead of the one intended.
    """

    def test_both_symbols_survive_a_duplicate_label_index(self):
        today = datetime.date(2026, 8, 24)
        old_dates = pd.date_range(
            end=pd.Timestamp(today) - pd.Timedelta(days=400),
            periods=6, freq='D').date

        df_a = pd.DataFrame({'Date': old_dates, 'Symbol': 'A',
                             'Value': range(6)})
        df_b = pd.DataFrame({'Date': old_dates, 'Symbol': 'B',
                             'Value': range(100, 106)})
        # Both blocks independently indexed 0..5 -- deliberately non-unique
        # once concatenated, as would happen from pd.concat(..., ignore_index=False)
        # on two per-symbol frames built separately.
        df_a.index = range(6)
        df_b.index = range(6)
        df = pd.concat([df_a, df_b])
        self.assertFalse(df.index.is_unique)  # sanity: this IS the trigger

        out = downsample_history(df, group_cols=('Symbol',))

        self.assertEqual(set(out['Symbol']), {'A', 'B'})


class TestTopNSymbols(unittest.TestCase):

    def test_picks_largest_absolute_movers(self):
        df = pd.DataFrame([
            {'Symbol': 'FLAT', 'ClosingPrice % Change': 0.5},
            {'Symbol': 'UP',   'ClosingPrice % Change': 90.0},
            {'Symbol': 'DOWN', 'ClosingPrice % Change': -80.0},
            {'Symbol': 'MEH',  'ClosingPrice % Change': 2.0},
        ])
        out = top_n_symbols(df, n=2)
        self.assertEqual(set(out), {'UP', 'DOWN'})

    def test_n_larger_than_available_returns_everything(self):
        df = pd.DataFrame([{'Symbol': 'ONLY', 'ClosingPrice % Change': 1.0}])
        self.assertEqual(top_n_symbols(df, n=10), ['ONLY'])


if __name__ == '__main__':
    unittest.main()
