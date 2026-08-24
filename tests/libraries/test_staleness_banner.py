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

    def test_fresh_history_and_fresh_prices_show_no_banner(self):
        self.assertIsNone(
            build_staleness_banner(datetime.date(2026, 8, 24), is_stale=False,
                                   price_fetched_at=datetime.datetime(2026, 8, 24, 12),
                                   is_price_stale=False))

    def test_stale_price_snapshot_shows_its_own_message(self):
        banner = build_staleness_banner(
            datetime.date(2026, 8, 24), is_stale=False,
            price_fetched_at=datetime.datetime(2026, 8, 18, 9),
            is_price_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('price_snapshot', str(banner.children))
        self.assertIn('2026-08-18', str(banner.children))

    def test_missing_price_snapshot_names_the_job(self):
        banner = build_staleness_banner(
            datetime.date(2026, 8, 24), is_stale=False,
            price_fetched_at=None, is_price_stale=True)
        self.assertIsNotNone(banner)
        self.assertIn('price_snapshot', str(banner.children))

    def test_both_stale_shows_both_messages_in_one_banner(self):
        banner = build_staleness_banner(
            datetime.date(2026, 8, 18), is_stale=True,
            price_fetched_at=datetime.datetime(2026, 8, 18, 9),
            is_price_stale=True)
        self.assertIsNotNone(banner)
        text = str(banner.children)
        self.assertIn('daily_update', text)
        self.assertIn('price_snapshot', text)


if __name__ == '__main__':
    unittest.main()
