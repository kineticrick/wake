import logging
import unittest
from unittest import mock

import pandas as pd

from generators import daily_update


class TestDailyUpdateOrchestration(unittest.TestCase):
    """
    daily_update must construct every handler in WRITE mode and in dependency
    order, because Portfolio and Hypothetical history are both derived from
    the freshly-updated asset history.
    """

    def setUp(self):
        self.logger = logging.getLogger('test_daily_update')
        self.logger.addHandler(logging.NullHandler())
        self.assets_df = pd.DataFrame([
            {'Date': '2026-08-24', 'Symbol': 'AAPL', 'Quantity': 1.0,
             'CostBasis': 100.0, 'ClosingPrice': 150.0, 'Value': 150.0},
        ])

    def _patched(self):
        """Patch every handler class with a recording fake."""
        order = []

        def make(name, attrs=None):
            class Fake:
                def __init__(self, *args, **kwargs):
                    order.append((name, kwargs.get('read_only')))
                    self.history_df = self.assets_df_ref
            Fake.assets_df_ref = self.assets_df
            return Fake

        patches = {
            name: mock.patch.object(daily_update, name, make(name))
            for name in ('AssetHistoryHandler', 'PortfolioHistoryHandler',
                         'AssetHypotheticalHistoryHandler', 'SectorHistoryHandler',
                         'AssetTypeHistoryHandler', 'AccountTypeHistoryHandler',
                         'GeographyHistoryHandler')
        }
        return order, patches

    def test_all_handlers_run_in_write_mode(self):
        order, patches = self._patched()
        for p in patches.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches.values()])

        with mock.patch.object(daily_update, 'table_max_dates',
                               return_value={'assets_history': '2026-08-24'}):
            daily_update.run_update(self.logger)

        names = [n for n, _ in order]
        self.assertEqual(len(names), 7)
        # read_only must be explicitly False for every handler, never None --
        # None would inherit PORTFOLIO_READ_ONLY and silently no-op the updater.
        self.assertTrue(all(ro is False for _, ro in order),
                        f"handlers not all in write mode: {order}")

    def test_asset_history_runs_before_its_dependents(self):
        order, patches = self._patched()
        for p in patches.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches.values()])

        with mock.patch.object(daily_update, 'table_max_dates', return_value={}):
            daily_update.run_update(self.logger)

        names = [n for n, _ in order]
        self.assertLess(names.index('AssetHistoryHandler'),
                        names.index('PortfolioHistoryHandler'))
        self.assertLess(names.index('AssetHistoryHandler'),
                        names.index('AssetHypotheticalHistoryHandler'))

    def test_failure_marks_the_run_failed_and_exits_nonzero(self):
        with mock.patch.object(daily_update, 'gen_history_meta_table'), \
             mock.patch.object(daily_update, 'start_run', return_value=7), \
             mock.patch.object(daily_update, 'run_update',
                               side_effect=RuntimeError('yfinance down')), \
             mock.patch.object(daily_update, 'fail_run') as fail, \
             mock.patch.object(daily_update, 'finish_run') as finish:
            code = daily_update.main([])

        self.assertNotEqual(code, 0)
        fail.assert_called_once()
        self.assertEqual(fail.call_args[0][0], 7)
        finish.assert_not_called()

    def test_success_marks_the_run_successful(self):
        with mock.patch.object(daily_update, 'gen_history_meta_table'), \
             mock.patch.object(daily_update, 'start_run', return_value=9), \
             mock.patch.object(daily_update, 'run_update',
                               return_value={'assets_history': '2026-08-24'}), \
             mock.patch.object(daily_update, 'fail_run') as fail, \
             mock.patch.object(daily_update, 'finish_run') as finish:
            code = daily_update.main([])

        self.assertEqual(code, 0)
        finish.assert_called_once_with(9, {'assets_history': '2026-08-24'})
        fail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
