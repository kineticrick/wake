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

    def test_recent_window_keeps_daily_resolution(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        cutoff = self.today - datetime.timedelta(days=365)
        recent_in = len(self.df[self.df['Date'] >= cutoff])
        recent_out = len(out[out['Date'] >= cutoff])
        self.assertEqual(recent_out, recent_in)

    def test_old_data_is_thinned(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
        self.assertLess(len(out), len(self.df))
        # Weekly beyond a year: roughly 1/7th of the old rows survive.
        cutoff = self.today - datetime.timedelta(days=365)
        old_in = len(self.df[self.df['Date'] < cutoff])
        old_out = len(out[out['Date'] < cutoff])
        self.assertLess(old_out, old_in / 3)

    def test_first_and_last_point_per_group_are_preserved(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 window_days=365, today=self.today)
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
                                 window_days=365, today=self.today)
        self.assertEqual(len(out), len(recent))

    def test_empty_frame_passes_through(self):
        empty = pd.DataFrame(columns=['Date', 'Symbol', 'Value'])
        self.assertTrue(downsample_history(empty, group_cols=('Symbol',)).empty)


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
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'),
                                 window_days=365, today=self.today)
        pairs_in = self.df[['Symbol', 'AccountType']].drop_duplicates()
        pairs_out = out[['Symbol', 'AccountType']].drop_duplicates()
        self.assertEqual(len(pairs_out), len(pairs_in))

    def test_endpoints_do_not_cross_accounts(self):
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'),
                                 window_days=365, today=self.today)
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
        out = downsample_history(self.df, group_cols=('Symbol', 'AccountType'),
                                 window_days=365, today=self.today)
        self.assertLess(len(out), len(self.df))


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
