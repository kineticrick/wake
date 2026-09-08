import shutil
import tempfile
import unittest
from unittest import mock

from diskcache import Cache

from libraries.db import mysql_helpers


class TestMemoizeEvictMechanism(unittest.TestCase):
    """
    Proves the assumption that actually failed: that a memoized read really
    does keep serving pre-write data until something evicts it.

    generators/importer.py wrote 19 new trades and never invalidated, so
    build_master_log() kept returning the pre-import log for the full 4-hour
    TTL. Summary validation reported 19 phantom quantity mismatches and 4
    phantom missing assets, and the nightly updater was one scheduled run away
    from computing history from trades that omitted them -- then persisting it.

    Uses a throwaway cache directory rather than the repo's real one, so this
    can never disturb live data.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = Cache(self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(self.cache.close)

        self.rows = ['trade-1']
        tag = 'testtag'

        @self.cache.memoize(expire=3600, tag=tag)
        def read_trades(query):
            return list(self.rows)

        self.read_trades = read_trades
        self.tag = tag

    def test_a_write_without_eviction_stays_invisible(self):
        # This is the bug, reproduced in miniature.
        self.assertEqual(self.read_trades('SELECT * FROM trades'), ['trade-1'])

        self.rows.append('trade-2')          # a writer adds a trade

        self.assertEqual(self.read_trades('SELECT * FROM trades'), ['trade-1'],
                         "expected the stale cached read -- if this now sees "
                         "trade-2, memoization is not behaving as the fix "
                         "assumes and the fix's premise is wrong")

    def test_eviction_makes_the_write_visible(self):
        self.read_trades('SELECT * FROM trades')
        self.rows.append('trade-2')

        self.cache.evict(tag=self.tag)

        self.assertEqual(self.read_trades('SELECT * FROM trades'),
                         ['trade-1', 'trade-2'])


class TestInvalidateQueryCache(unittest.TestCase):

    def test_evicts_the_tag_that_every_query_is_written_under(self):
        # mysql_query memoizes with tag=MYSQL_CACHE_HISTORY_TAG for ALL
        # queries, not just history ones -- so evicting that tag is what
        # clears trades/dividends/splits/entities/summary too. If the tag
        # ever diverges, writers would silently stop invalidating reads.
        with mock.patch.object(mysql_helpers, 'mysql_cache_evict') as evict:
            mysql_helpers.invalidate_query_cache()

        evict.assert_called_once_with(mysql_helpers.MYSQL_CACHE_HISTORY_TAG)


class TestWritersInvalidate(unittest.TestCase):
    """
    Every writer to a table mysql_query reads must invalidate. These assert the
    call exists at all -- the failure mode was its total absence, which no test
    noticed for the life of the importer.
    """

    def test_importer_invalidates_after_writing(self):
        import generators.importer as importer
        self.assertTrue(
            hasattr(importer, 'invalidate_query_cache'),
            "importer.py must import invalidate_query_cache")

        source = __import__('inspect').getsource(importer.main)
        self.assertIn('invalidate_query_cache()', source,
                      "importer.main() writes trades but never invalidates "
                      "the read cache")

    def test_summary_writer_invalidates_after_writing(self):
        from generators import generator_helpers
        source = __import__('inspect').getsource(generator_helpers.write_db)
        self.assertIn('invalidate_query_cache()', source,
                      "write_db() rewrites the summary table but never "
                      "invalidates the read cache")


if __name__ == '__main__':
    unittest.main()
