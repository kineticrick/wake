import datetime
import unittest

import pandas as pd

from libraries.HistoryHandlers.BaseHistoryHandler import BaseHistoryHandler


def _make_handler_class(calls, history_df):
    """A handler that records which lifecycle methods actually ran."""

    class FakeHandler(BaseHistoryHandler):
        def gen_table(self_inner):
            calls.append('gen_table')

        def get_history(self_inner):
            calls.append('get_history')
            return history_df.copy()

        def set_history(self_inner, start_date=None, overwrite=False):
            calls.append('set_history')
            raise AssertionError(
                "set_history() must never run in read-only mode — it is the "
                "code path that calls yfinance from inside a Dash callback.")

    return FakeHandler


class TestReadOnlyMode(unittest.TestCase):

    def setUp(self):
        # Deliberately stale: 40 days behind, so write mode WOULD try to update.
        self.stale_date = datetime.date.today() - datetime.timedelta(days=40)
        self.stale_df = pd.DataFrame({'Date': [self.stale_date]})

    def test_read_only_skips_ddl_and_writes_even_when_stale(self):
        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        handler = FakeHandler(read_only=True)

        self.assertEqual(calls, ['get_history'])
        self.assertNotIn('set_history', calls)
        self.assertNotIn('gen_table', calls)

    def test_read_only_still_exposes_latest_history_date(self):
        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        handler = FakeHandler(read_only=True)

        self.assertEqual(handler.latest_history_date, self.stale_date)
        self.assertTrue(handler.read_only)

    def test_read_only_tolerates_an_empty_table(self):
        calls = []
        empty = pd.DataFrame(columns=['Date'])
        FakeHandler = _make_handler_class(calls, empty)

        handler = FakeHandler(read_only=True)

        self.assertIsNone(handler.latest_history_date)
        self.assertTrue(handler.history_df.empty)

    def test_read_only_defaults_from_global(self):
        # NOTE: can't use `import libraries.HistoryHandlers.BaseHistoryHandler
        # as base_mod` here -- libraries/HistoryHandlers/__init__.py does
        # `from .BaseHistoryHandler import BaseHistoryHandler`, which shadows
        # the submodule name in the package namespace with the class, so that
        # form binds to the class rather than the module. Go through
        # sys.modules to reach the actual module object (already loaded via
        # the `from ... import BaseHistoryHandler` at the top of this file).
        import sys
        base_mod = sys.modules['libraries.HistoryHandlers.BaseHistoryHandler']

        calls = []
        FakeHandler = _make_handler_class(calls, self.stale_df)

        original = base_mod.PORTFOLIO_READ_ONLY
        base_mod.PORTFOLIO_READ_ONLY = True
        try:
            FakeHandler()          # no explicit argument
        finally:
            base_mod.PORTFOLIO_READ_ONLY = original

        self.assertEqual(calls, ['get_history'])


if __name__ == '__main__':
    unittest.main()
