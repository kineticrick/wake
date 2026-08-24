import logging
import unittest
from unittest import mock

import pandas as pd

from generators import price_snapshot


class TestBuildSnapshotValuesDropsNullPrices(unittest.TestCase):
    """
    CRITICAL 2 regression: a missing yfinance quote comes back as NaN in a
    float column, not None. `nan is not None` is True, so the original
    `is not None` filter never fired and NaN reached executemany(), which
    MySQL rejects with a ProgrammingError that takes the whole batch down
    with it. pd.notna() must actually catch it.
    """

    def setUp(self):
        self.logger = logging.getLogger('test_price_snapshot')
        self.logger.addHandler(logging.NullHandler())
        self.fetched_at = mock.sentinel.fetched_at

    def test_nan_price_is_dropped_not_written(self):
        prices_df = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 150.0},
            {'Symbol': 'HALT', 'Current Price': float('nan')},
            {'Symbol': 'MSFT', 'Current Price': 420.0},
        ])

        values = price_snapshot._build_snapshot_values(
            prices_df, self.fetched_at, self.logger)

        symbols_written = {row[0] for row in values}
        self.assertEqual(symbols_written, {'AAPL', 'MSFT'})
        self.assertEqual(len(values), 2)

    def test_nan_price_does_not_raise(self):
        prices_df = pd.DataFrame([
            {'Symbol': 'HALT', 'Current Price': float('nan')},
        ])
        try:
            values = price_snapshot._build_snapshot_values(
                prices_df, self.fetched_at, self.logger)
        except Exception as exc:                      # noqa: BLE001
            self.fail(f"_build_snapshot_values raised on a NaN price: {exc}")
        self.assertEqual(values, [])

    def test_nan_price_is_logged(self):
        prices_df = pd.DataFrame([
            {'Symbol': 'HALT', 'Current Price': float('nan')},
        ])
        with self.assertLogs('test_price_snapshot', level='WARNING') as cm:
            price_snapshot._build_snapshot_values(
                prices_df, self.fetched_at, self.logger)
        self.assertTrue(any('HALT' in line for line in cm.output))


class TestWritePathDegradesGracefully(unittest.TestCase):
    """
    CRITICAL 2 regression: the executemany() write used to sit outside any
    try/except, so a DB error on write (a NaN reaching MySQL, a transient
    connection failure, anything) escaped main() unhandled and lost the
    whole ~30-price batch instead of degrading to "keep the previous
    snapshot" like every other failure mode in this job.
    """

    def setUp(self):
        self.summary_df = pd.DataFrame([{'Symbol': 'AAPL'}, {'Symbol': 'MSFT'}])
        self.prices_df = pd.DataFrame([
            {'Symbol': 'AAPL', 'Current Price': 150.0},
            {'Symbol': 'MSFT', 'Current Price': 420.0},
        ])

    def test_executemany_failure_returns_1_and_does_not_raise(self):
        fake_db = mock.MagicMock()
        fake_db.__enter__.return_value = fake_db
        fake_db.cursor.executemany.side_effect = RuntimeError(
            "1054 Unknown column 'nan' in 'field list'")

        with mock.patch.object(price_snapshot, 'PORTFOLIO_READ_ONLY', False), \
             mock.patch.object(price_snapshot, 'MysqlDB', return_value=fake_db), \
             mock.patch.object(price_snapshot, 'get_portfolio_summary',
                               return_value=self.summary_df), \
             mock.patch.object(price_snapshot, 'get_current_price',
                               return_value=self.prices_df):
            try:
                code = price_snapshot.main()
            except Exception as exc:                  # noqa: BLE001
                self.fail(f"main() let a write failure escape unhandled: {exc}")

        self.assertEqual(code, 1)

    def test_successful_write_returns_0(self):
        fake_db = mock.MagicMock()
        fake_db.__enter__.return_value = fake_db

        with mock.patch.object(price_snapshot, 'PORTFOLIO_READ_ONLY', False), \
             mock.patch.object(price_snapshot, 'MysqlDB', return_value=fake_db), \
             mock.patch.object(price_snapshot, 'get_portfolio_summary',
                               return_value=self.summary_df), \
             mock.patch.object(price_snapshot, 'get_current_price',
                               return_value=self.prices_df):
            code = price_snapshot.main()

        self.assertEqual(code, 0)
        fake_db.cursor.executemany.assert_called_once()


if __name__ == '__main__':
    unittest.main()
