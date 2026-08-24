import datetime
import unittest

from libraries.db.history_meta import compute_staleness


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


if __name__ == '__main__':
    unittest.main()
