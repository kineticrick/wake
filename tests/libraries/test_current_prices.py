import unittest
from unittest import mock

import pandas as pd

import libraries.yfinance_helpers.yfinancelib as yfl


class TestGetCurrentPriceReadOnly(unittest.TestCase):

    def test_read_only_reads_db_and_never_calls_yfinance(self):
        db_frame = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 150.0},
            {'Symbol': 'MSFT', 'Current Price': 420.0},
        ])

        def explode(*a, **kw):
            raise AssertionError(
                "yfinance must not be called when PORTFOLIO_READ_ONLY=1")

        with mock.patch.object(yfl, 'PORTFOLIO_READ_ONLY', True), \
             mock.patch.object(yfl, 'read_current_prices_from_db',
                               return_value=db_frame) as read_db, \
             mock.patch.object(yfl, '_gen_current_prices', side_effect=explode):
            out = yfl.get_current_price(['AAPL', 'MSFT'])

        read_db.assert_called_once_with(['AAPL', 'MSFT'])
        self.assertEqual(list(out.columns), ['Symbol', 'Current Price'])
        self.assertEqual(len(out), 2)

    def test_write_mode_still_uses_yfinance(self):
        with mock.patch.object(yfl, 'PORTFOLIO_READ_ONLY', False), \
             mock.patch.object(yfl, '_gen_current_prices',
                               return_value=[{'Symbol': 'AAPL',
                                              'Current Price': 150.0}]) as gen:
            out = yfl.get_current_price(['AAPL'])

        gen.assert_called_once_with(['AAPL'])
        self.assertEqual(out.iloc[0]['Current Price'], 150.0)


class TestSnapshotFallbackToLastClose(unittest.TestCase):
    """
    A symbol with no snapshot row must degrade to its last closing price, not
    to NaN -- otherwise the whole portfolio total silently becomes NaN.
    """

    def test_missing_symbol_falls_back_to_last_close(self):
        snapshot = pd.DataFrame([{'Symbol': 'AAPL', 'Current Price': 150.0}])
        closes = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 148.0},
            {'Symbol': 'NEW',  'Current Price': 20.0},
        ])

        def fake_mysql_to_df(query, columns, cfg, cached=False, verbose=False):
            return snapshot.copy() if 'current_prices' in query else closes.copy()

        with mock.patch.object(yfl, 'mysql_to_df', side_effect=fake_mysql_to_df):
            out = yfl.read_current_prices_from_db(['AAPL', 'NEW'])

        by_symbol = dict(zip(out['Symbol'], out['Current Price']))
        # AAPL has a snapshot, so the snapshot wins over the stale close.
        self.assertEqual(by_symbol['AAPL'], 150.0)
        # NEW has none, so it falls back rather than going missing.
        self.assertEqual(by_symbol['NEW'], 20.0)

    def test_empty_snapshot_falls_back_entirely(self):
        empty = pd.DataFrame(columns=['Symbol', 'Current Price'])
        closes = pd.DataFrame([{'Symbol': 'AAPL', 'Current Price': 148.0}])

        def fake_mysql_to_df(query, columns, cfg, cached=False, verbose=False):
            return empty.copy() if 'current_prices' in query else closes.copy()

        with mock.patch.object(yfl, 'mysql_to_df', side_effect=fake_mysql_to_df):
            out = yfl.read_current_prices_from_db(['AAPL'])

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]['Current Price'], 148.0)


if __name__ == '__main__':
    unittest.main()
