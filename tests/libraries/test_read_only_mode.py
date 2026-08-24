import datetime
import unittest
from unittest import mock

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


class TestNestedHandlerInheritsReadOnly(unittest.TestCase):
    """
    Regression test: AssetHypotheticalHistoryHandler and PortfolioHistoryHandler
    each construct a nested AssetHistoryHandler on demand, when the caller
    doesn't supply assets_history_df. Before this fix, that nested
    construction used AssetHistoryHandler()'s own read_only=None default,
    which falls back to the process-wide global -- so a caller who explicitly
    asked for read_only=True got a nested handler that ignored that explicit
    request whenever the global happened to disagree (e.g. False, the
    default in a write-mode process), triggering the very yfinance stall
    read-only mode exists to prevent. The fix: both parents resolve their own
    `self.read_only` up front (via BaseHistoryHandler._resolve_read_only) and
    forward it explicitly to the nested handler.
    """

    def test_asset_hypothetical_handler_forwards_explicit_read_only(self):
        # Same package-namespace shadowing as test_read_only_defaults_from_global
        # above (HistoryHandlers/__init__.py rebinds the submodule name to the
        # class); go through sys.modules for the actual module object.
        import sys
        ahh_mod = sys.modules[
            'libraries.HistoryHandlers.AssetHypotheticalHistoryHandler']

        captured = {}

        class FakeNestedAssetHistoryHandler:
            def __init__(self_inner, symbols, read_only=None):
                captured['read_only'] = read_only
                if not read_only:
                    raise AssertionError(
                        "nested AssetHistoryHandler must inherit the "
                        "parent's explicit read_only=True, not fall back "
                        "to the global")
                self_inner.history_df = pd.DataFrame(
                    columns=['Date', 'Symbol', 'AccountType', 'Quantity',
                             'CostBasis', 'ClosingPrice', 'Value',
                             'PercentReturn'])

        empty_quantities = pd.DataFrame(
            columns=['Symbol', 'Quantity']).rename_axis('Date')

        class FakeHypotheticalHandler(ahh_mod.AssetHypotheticalHistoryHandler):
            def get_history(self_inner):
                # Stand-in for the real (DB-backed) get_history(); read-only
                # mode calls this and nothing else once past __init__'s
                # pre-super() setup.
                return pd.DataFrame(columns=['Date', 'Symbol', 'Quantity',
                                              'ClosingPrice', 'Value', 'Owned'])

        with mock.patch.object(ahh_mod, 'AssetHistoryHandler',
                                FakeNestedAssetHistoryHandler), \
             mock.patch.object(ahh_mod, 'build_master_log',
                                return_value=pd.DataFrame()), \
             mock.patch.object(ahh_mod, 'gen_hist_quantities_mult',
                                return_value=empty_quantities):
            handler = FakeHypotheticalHandler(read_only=True)

        self.assertTrue(handler.read_only)
        self.assertIs(captured['read_only'], True)

    def test_portfolio_handler_forwards_explicit_read_only(self):
        # Same package-namespace shadowing as above; go through sys.modules.
        import sys
        ph_mod = sys.modules['libraries.HistoryHandlers.PortfolioHistoryHandler']

        captured = {}

        class FakeNestedAssetHistoryHandler:
            def __init__(self_inner, read_only=None):
                captured['read_only'] = read_only
                if not read_only:
                    raise AssertionError(
                        "nested AssetHistoryHandler must inherit the "
                        "parent's explicit read_only=True, not fall back "
                        "to the global")
                self_inner.history_df = pd.DataFrame(
                    columns=['Date', 'Value', 'CostBasis'])

        class FakeMysqlDB:
            """No-op stand-in so the unrelated DB-write tail of
            set_history() (unchanged by this fix, and not itself gated by
            read_only) doesn't attempt a real connection during this test."""
            def __init__(self_inner, cfg):
                pass

            def __enter__(self_inner):
                self_inner.cursor = mock.MagicMock()
                return self_inner

            def __exit__(self_inner, *exc_info):
                return False

        class FakePortfolioHandler(ph_mod.PortfolioHistoryHandler):
            def get_history(self_inner):
                return pd.DataFrame(columns=['Date', 'Value', 'CostBasis'])

        handler = FakePortfolioHandler(read_only=True)
        self.assertTrue(handler.read_only)

        # set_history() is never called automatically in read-only mode (see
        # TestReadOnlyMode above); this directly exercises the nested
        # construction inside it to confirm the forwarding wiring itself,
        # covering direct/out-of-band calls to set_history() as well.
        with mock.patch.object(ph_mod, 'AssetHistoryHandler',
                                FakeNestedAssetHistoryHandler), \
             mock.patch.object(ph_mod, 'MysqlDB', FakeMysqlDB):
            handler.set_history()

        self.assertIs(captured['read_only'], True)


if __name__ == '__main__':
    unittest.main()
