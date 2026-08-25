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

def mysql_cache_evict(cache_tag: str) -> None:
    """
    Evict all items with given tag from cache
    """
    cache.evict(tag=cache_tag)