import datetime
import unittest
from unittest import mock

from libraries.db import history_meta


class TestAbandonStaleRuns(unittest.TestCase):
    """
    A run killed mid-flight (suspend, OOM, TimeoutStartSec, Ctrl-C) never
    reaches finish_run() or fail_run(), so its history_meta row stays 'running'
    forever. Nothing reported those rows, which meant a genuinely hung updater
    left exactly the same trace as one that had long since died.

    The sweep must be bounded: clobbering a legitimately in-flight run would be
    worse than the problem it fixes, because a concurrent manual run would be
    recorded as failed while it was still working.
    """

    def _capture_execute(self):
        """Patch MysqlDB and return the list that receives (sql, params)."""
        calls = []

        class FakeCursor:
            rowcount = 1
            lastrowid = 42

        class FakeDB:
            cursor = FakeCursor()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def execute(self_inner, sql, params=None):
                calls.append((sql, params))

        patcher = mock.patch.object(history_meta, 'MysqlDB',
                                    lambda cfg: FakeDB())
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_cutoff_is_the_systemd_timeout_before_now(self):
        calls = self._capture_execute()
        now = datetime.datetime(2026, 9, 7, 15, 0, 0)

        history_meta.abandon_stale_runs(now=now)

        _sql, params = calls[0]
        swept_at, _note, cutoff = params
        self.assertEqual(swept_at, now)
        # 1800s == the unit's TimeoutStartSec, so a run systemd would itself
        # have killed is exactly the one swept.
        self.assertEqual(now - cutoff, datetime.timedelta(seconds=1800))

    def test_only_running_rows_older_than_the_cutoff_are_swept(self):
        calls = self._capture_execute()

        history_meta.abandon_stale_runs(now=datetime.datetime(2026, 9, 7, 15, 0))

        sql, _params = calls[0]
        # The bound is what keeps an in-flight run safe. Without BOTH clauses a
        # concurrent manual run would be marked failed while still working.
        self.assertIn("status = 'running'", sql)
        self.assertIn("run_started <", sql)

    def test_the_recorded_error_explains_what_happened(self):
        calls = self._capture_execute()

        history_meta.abandon_stale_runs(now=datetime.datetime(2026, 9, 7, 15, 0))

        _sql, params = calls[0]
        note = params[1]
        self.assertIn('Abandoned', note)
        # A bare 'failed' with no error text is what made the original row
        # uninterpretable; the note must say why it was swept.
        self.assertTrue(len(note) > 40, f"note is too terse: {note!r}")

    def test_returns_the_number_of_rows_swept(self):
        self._capture_execute()

        swept = history_meta.abandon_stale_runs(
            now=datetime.datetime(2026, 9, 7, 15, 0))

        self.assertEqual(swept, 1)

    def test_start_run_sweeps_before_inserting(self):
        """The sweep is worthless if it runs after the new row is created."""
        order = []

        with mock.patch.object(history_meta, 'abandon_stale_runs',
                               side_effect=lambda: order.append('sweep')):
            calls = self._capture_execute()
            original_len = len(calls)
            history_meta.start_run()
            if len(calls) > original_len:
                order.append('insert')

        self.assertEqual(order, ['sweep', 'insert'])


if __name__ == '__main__':
    unittest.main()
