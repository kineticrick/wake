import datetime
import unittest

from libraries.db.history_meta import compute_run_staleness, lagging_tables


class TestComputeRunStaleness(unittest.TestCase):
    """
    Staleness is judged on when the updater last SUCCEEDED, not on the data's
    date.

    The previous rule compared the newest data date against pandas' BDay(1),
    which knows weekends but not market holidays. On Tuesday 2026-09-08 it
    demanded data for Monday 2026-09-07 -- Labor Day, when the market never
    traded -- so the banner fired with everything perfectly up to date. It
    would have done the same every Thanksgiving, July 4th and Christmas.

    Keying off the run works because the systemd timer fires on holidays too:
    the job runs, finds no new trading day, writes nothing, and still records a
    success. A healthy system therefore produces a successful run every
    weekday regardless of the market calendar.

    Every case passes `today` explicitly so these never depend on the clock.
    """

    def test_the_labor_day_case_that_produced_a_false_banner(self):
        # The actual reported bug: last success Mon 2026-09-07 (Labor Day --
        # the timer fired, the job no-opped and succeeded), checked on Tue
        # 2026-09-08. One business day apart: fresh.
        self.assertFalse(compute_run_staleness(
            datetime.datetime(2026, 9, 7, 16, 30),
            today=datetime.date(2026, 9, 8)))

    def test_never_run_is_stale(self):
        self.assertTrue(compute_run_staleness(
            None, today=datetime.date(2026, 9, 8)))

    def test_run_today_is_fresh(self):
        self.assertFalse(compute_run_staleness(
            datetime.datetime(2026, 9, 8, 16, 30),
            today=datetime.date(2026, 9, 8)))

    def test_friday_run_checked_on_monday_is_fresh(self):
        # The weekend must not trip the banner.
        self.assertFalse(compute_run_staleness(
            datetime.datetime(2026, 9, 4, 16, 30),
            today=datetime.date(2026, 9, 7)))

    def test_friday_run_checked_on_tuesday_is_stale(self):
        # Monday's run genuinely did not happen -- and a holiday is no excuse,
        # because the timer fires on holidays too. This is a real failure.
        self.assertTrue(compute_run_staleness(
            datetime.datetime(2026, 9, 4, 16, 30),
            today=datetime.date(2026, 9, 8)))

    def test_a_week_old_run_is_stale(self):
        self.assertTrue(compute_run_staleness(
            datetime.datetime(2026, 9, 1, 16, 30),
            today=datetime.date(2026, 9, 8)))

    def test_accepts_a_plain_date_as_well_as_a_datetime(self):
        self.assertFalse(compute_run_staleness(
            datetime.date(2026, 9, 7), today=datetime.date(2026, 9, 8)))


class TestLaggingTables(unittest.TestCase):
    """
    A table drifting behind the others used to be invisible: as_of was the
    OLDEST per-table date, so one lagging table silently spoke for all seven
    and the banner blamed the whole pipeline. This is the real observed case --
    assets_hypothetical_history stopped at Friday while six others reached
    Sunday.
    """

    OBSERVED = {
        'assets_history': '2026-09-06',
        'sectors_history': '2026-09-06',
        'geography_history': '2026-09-06',
        'portfolio_history': '2026-09-06',
        'asset_types_history': '2026-09-06',
        'account_types_history': '2026-09-06',
        'assets_hypothetical_history': '2026-09-04',
    }

    def test_names_only_the_table_that_is_behind(self):
        self.assertEqual(lagging_tables(self.OBSERVED),
                         ['assets_hypothetical_history'])

    def test_all_current_means_nothing_is_lagging(self):
        even = {k: '2026-09-06' for k in self.OBSERVED}
        self.assertEqual(lagging_tables(even), [])

    def test_orders_by_how_far_behind(self):
        mixed = dict(self.OBSERVED)
        mixed['sectors_history'] = '2026-09-05'
        self.assertEqual(
            lagging_tables(mixed),
            ['assets_hypothetical_history', 'sectors_history'])

    def test_empty_and_null_inputs_do_not_raise(self):
        self.assertEqual(lagging_tables({}), [])
        self.assertEqual(lagging_tables(None), [])
        self.assertEqual(lagging_tables({'a': None}), [])


if __name__ == '__main__':
    unittest.main()
