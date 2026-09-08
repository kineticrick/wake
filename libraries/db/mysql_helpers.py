from libraries.db import MysqlDB
from libraries.globals import (MYSQL_CACHE_TTL, MYSQL_CACHE_HISTORY_TAG,
                               ensure_cache_dir)
from diskcache import Cache

cache = Cache(ensure_cache_dir())

@cache.memoize(expire=MYSQL_CACHE_TTL, tag=MYSQL_CACHE_HISTORY_TAG)
def mysql_query(query, dbcfg, verbose=False):
    if verbose: 
        print(f"Query: {query}")
    
    with MysqlDB(dbcfg) as db:
        return db.query(query)

def invalidate_query_cache() -> None:
    """
    Drop every memoized DB read. Call after ANY write to a table mysql_query
    reads -- trades, dividends, splits, entities, acquisitions, summary.

    mysql_query memoizes on the query string alone, for MYSQL_CACHE_TTL (4
    hours), with no way to know the underlying rows changed. So a writer that
    does not invalidate leaves every reader serving pre-write data until the
    TTL lapses.

    This is not theoretical. generators/importer.py wrote 19 new trades and
    never invalidated: build_master_log() kept returning the pre-import log,
    so summary validation reported 19 phantom quantity mismatches and 4
    phantom missing assets, and the nightly updater was one scheduled run
    away from computing history from trades that omitted them -- then writing
    that history to disk, where it would have outlived the cache entirely.

    Named separately from mysql_cache_evict() because the tag actually in use
    (MYSQL_CACHE_HISTORY_TAG) is applied to EVERY memoized query, not only
    history ones. "Evict the history tag" therefore means "evict everything",
    which is what a writer wants but not what the name suggests.
    """
    mysql_cache_evict(MYSQL_CACHE_HISTORY_TAG)


def mysql_cache_evict(cache_tag: str) -> None:
    """
    Evict all items with given tag from cache
    """
    cache.evict(tag=cache_tag)