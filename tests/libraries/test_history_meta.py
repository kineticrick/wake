import datetime
import unittest
from unittest import mock

from libraries.db import history_meta
from libraries.db.history_meta import (compute_staleness,
                                       compute_price_staleness,
                                       latest_price_snapshot)


class TestComputeStaleness(unittest.TestCase):
    """
    Staleness is 'is the data behind the most recent completed trading day'.
    Every case passes `today` explicitly so the test never depends on when it runs.
    """

    def test_no_data_at_all_is_stale(self):
        self.assertTrue(
            compute_staleness(None, today=datetime.date(2026, 8, 25)))

    def test_data_from_previous_business_day_is_fresh(self):
        # Tuesday 2026-08-25; previous business day is Monday 2026-08-24.
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 24),
                              today=datetime.date(2026, 8, 25)))

    def test_data_from_today_is_fresh(self):
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 25),
                              today=datetime.date(2026, 8, 25)))

    def test_data_from_last_week_is_stale(self):
        self.assertTrue(
            compute_staleness(datetime.date(2026, 8, 18),
                              today=datetime.date(2026, 8, 25)))

    def test_friday_data_is_fresh_on_monday(self):
        # Monday 2026-08-24; previous business day is Friday 2026-08-21.
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 21),
                              today=datetime.date(2026, 8, 24)))

    def test_friday_data_is_fresh_on_saturday(self):
        self.assertFalse(
            compute_staleness(datetime.date(2026, 8, 21),
                              today=datetime.date(2026, 8, 22)))

    def test_thursday_data_is_stale_on_monday(self):
        self.assertTrue(
            compute_staleness(datetime.date(2026, 8, 20),
                              today=datetime.date(2026, 8, 24)))


class TestComputePriceStaleness(unittest.TestCase):
    """
    IMPORTANT 3: price-snapshot staleness is a plain hour-count check (unlike
    compute_staleness's business-day logic) -- see PRICE_SNAPSHOT_STALE_HOURS
    in globals.py for why 72h. Every case passes `now` explicitly so the test
    never depends on the clock.
    """

    def test_no_snapshot_at_all_is_stale(self):
        self.assertTrue(
            compute_price_staleness(None, now=datetime.datetime(2026, 8, 25, 9)))

    def test_recent_snapshot_is_fresh(self):
        self.assertFalse(
            compute_price_staleness(
                datetime.datetime(2026, 8, 25, 8, 50),
                now=datetime.datetime(2026, 8, 25, 9)))

    def test_snapshot_from_over_a_weekend_ago_is_stale(self):
        # Six days old -- comfortably past a dead timer, not just a weekend gap.
        self.assertTrue(
            compute_price_staleness(
                datetime.datetime(2026, 8, 18, 16),
                now=datetime.datetime(2026, 8, 24, 9)))

    def test_normal_weekend_gap_does_not_false_alarm(self):
        # Friday 4pm close -> Monday 9am open is ~65 hours, inside the 72h
        # threshold: this must NOT be reported as stale.
        self.assertFalse(
            compute_price_staleness(
                datetime.datetime(2026, 8, 21, 16),
                now=datetime.datetime(2026, 8, 24, 9)))

    def test_overnight_gap_does_not_false_alarm(self):
        # Friday 4pm close -> next morning 8am is ~16 hours.
        self.assertFalse(
            compute_price_staleness(
                datetime.datetime(2026, 8, 21, 16),
                now=datetime.datetime(2026, 8, 22, 8)))


class TestLatestPriceSnapshot(unittest.TestCase):
    """latest_price_snapshot() is a thin DB read; mock MysqlDB so this stays
    a unit test rather than an integration test against a live connection."""

    def test_returns_the_queried_timestamp(self):
        fetched_at = datetime.datetime(2026, 8, 24, 12, 53, 36)

        class FakeCursor:
            def fetchone(self_inner):
                return (fetched_at,)

        class FakeDB:
            def __init__(self_inner, cfg):
                self_inner.cursor = FakeCursor()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc_info):
                return False

            def execute(self_inner, *a, **kw):
                pass

            def fetchone(self_inner):
                return (fetched_at,)

        with mock.patch.object(history_meta, 'MysqlDB', FakeDB):
            self.assertEqual(latest_price_snapshot(), fetched_at)

    def test_empty_table_returns_none(self):
        class FakeDB:
            def __init__(self_inner, cfg):
                pass

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc_info):
                return False

            def execute(self_inner, *a, **kw):
                pass

            def fetchone(self_inner):
                return (None,)

        with mock.patch.object(history_meta, 'MysqlDB', FakeDB):
            self.assertIsNone(latest_price_snapshot())


if __name__ == '__main__':
    unittest.main()
