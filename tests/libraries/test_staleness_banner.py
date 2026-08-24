import datetime
import unittest

from visualization.dash.portfolio_dashboard.banners import (
    build_staleness_banner)


class TestStalenessBanner(unittest.TestCase):

    def test_fresh_data_shows_no_banner(self):
        self.assertIsNone(
            build_staleness_banner(datetime.date(2026, 8, 24), is_stale=False))

    def test_stale_data_shows_the_as_of_date(self):
        banner = build_staleness_banner(datetime.date(2026, 8, 18),
                                        is_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('2026-08-18', str(banner.children))

    def test_no_data_at_all_names_the_updater(self):
        banner = build_staleness_banner(None, is_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('daily_update', str(banner.children))


if __name__ == '__main__':
    unittest.main()
