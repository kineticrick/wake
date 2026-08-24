import unittest
from unittest import mock

import pandas as pd

import libraries.helpers as helpers


class TestAggregationCacheBounding(unittest.TestCase):

    def setUp(self):
        helpers._aggregation_cache.clear()
        self.addCleanup(helpers._aggregation_cache.clear)

    def _fake_expanded(self):
        return pd.DataFrame([
            {'Date': '2026-08-24', 'Sector': 'Technology',
             'Value': 100.0, 'CostBasis': 80.0},
        ])

    def test_cache_never_exceeds_the_configured_maximum(self):
        max_entries = helpers.AGGREGATION_CACHE_MAX_ENTRIES

        with mock.patch.object(helpers, 'gen_assets_historical_value',
                               return_value=pd.DataFrame()), \
             mock.patch.object(helpers, 'add_asset_info',
                               return_value=self._fake_expanded()):
            for i in range(max_entries + 5):
                helpers.gen_aggregated_historical_value(
                    dimension='Sector', start_date=f'2026-01-{i % 28 + 1:02d}',
                    account_type=f'acct-{i}')

        self.assertLessEqual(len(helpers._aggregation_cache), max_entries)

    def test_recently_used_entry_survives_eviction(self):
        max_entries = helpers.AGGREGATION_CACHE_MAX_ENTRIES

        with mock.patch.object(helpers, 'gen_assets_historical_value',
                               return_value=pd.DataFrame()), \
             mock.patch.object(helpers, 'add_asset_info',
                               return_value=self._fake_expanded()):
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='keep-me')

            for i in range(max_entries - 1):
                helpers.gen_aggregated_historical_value(
                    dimension='Sector', account_type=f'filler-{i}')

            # Touch the first entry so it becomes most-recently-used...
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='keep-me')

            # ...then push one more in, which must evict a filler, not 'keep-me'.
            helpers.gen_aggregated_historical_value(
                dimension='Sector', account_type='overflow')

        keys = [k for k in helpers._aggregation_cache if 'keep-me' in str(k)]
        self.assertEqual(len(keys), 1)


if __name__ == '__main__':
    unittest.main()
