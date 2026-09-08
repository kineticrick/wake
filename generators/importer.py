#!/usr/bin/env python3
"""
Portfolio Transaction Importer (Optimized)

This is the optimized importer that provides 10-100x speedup through:
1. Bulk INSERT operations using executemany() instead of individual INSERTs
2. Single database connection instead of one per query
3. Parallel CSV processing across brokerages using ThreadPoolExecutor
4. Parameterized queries (prevents SQL injection)

Performance Comparison (for ~10,000 transactions):
- Original version (see importer_unoptimized.py): ~5-10 minutes
- This optimized version: ~5-30 seconds

See generators/OPTIMIZATION_NOTES.md for detailed explanation of optimizations.

Usage:
    python generators/importer.py
"""

import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from libraries.db.sql import *
from libraries.db.sql import transaction_table_indexes
from libraries.db import MysqlDB, dbcfg, invalidate_query_cache
from libraries.globals import FILEDIRS
from generators.generator_helpers import (build_file_lists,
                                          process_csvs,
                                          cleanup_transactions,
                                          validate_transactions)

def bulk_insert_acquisitions(db, acquisitions):
    """Bulk insert acquisitions using executemany"""
    if not acquisitions:
        return

    sql = """INSERT IGNORE INTO acquisitions
             (date, symbol, acquirer, conversion_ratio)
             VALUES (%s, %s, %s, %s)"""

    values = [(a['date'], a['symbol'], a['acquirer'], a['conversion_ratio'])
              for a in acquisitions]

    db.cursor.executemany(sql, values)
    print(f"Inserted {len(acquisitions)} acquisitions")

def bulk_insert_entities(db, entities):
    """Bulk insert entities using executemany"""
    if not entities:
        return

    sql = """INSERT IGNORE INTO entities
             (name, symbol, asset_type, sector, geography)
             VALUES (%s, %s, %s, %s, %s)"""

    values = [(e['name'], e['symbol'], e['asset_type'], e['sector'], e['geography'])
              for e in entities]

    db.cursor.executemany(sql, values)
    print(f"Inserted {len(entities)} entities")

def bulk_insert_splits(db, splits):
    """Bulk insert splits using executemany"""
    if not splits:
        return

    sql = """INSERT IGNORE INTO splits
             (record_date, distribution_date, symbol, multiplier)
             VALUES (%s, %s, %s, %s)"""

    values = [(s['record_date'], s['distribution_date'], s['symbol'], s['multiplier'])
              for s in splits]

    db.cursor.executemany(sql, values)
    print(f"Inserted {len(splits)} splits")

def bulk_insert_transactions(db, transactions):
    """Bulk insert trades and dividends using executemany"""
    if not transactions:
        return

    # Separate trades from dividends
    trades = [t for t in transactions if t['action'] in ('buy', 'sell')]
    dividends = [t for t in transactions if t['action'] == 'dividend']

    # Bulk insert trades
    if trades:
        trade_sql = """INSERT IGNORE INTO trades
                      (date, symbol, action, num_shares, price_per_share, total_price, account_type)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)"""

        trade_values = [(t['date'], t['symbol'], t['action'], t['num_shares'],
                        t['price_per_share'], t['total_price'], t['account_type'])
                       for t in trades]

        db.cursor.executemany(trade_sql, trade_values)
        print(f"Inserted {len(trades)} trades")

    # Bulk insert dividends
    if dividends:
        dividend_sql = """INSERT IGNORE INTO dividends
                         (date, symbol, dividend, account_type)
                         VALUES (%s, %s, %s, %s)"""

        dividend_values = [(d['date'], d['symbol'], d['dividend'], d['account_type'])
                          for d in dividends]

        db.cursor.executemany(dividend_sql, dividend_values)
        print(f"Inserted {len(dividends)} dividends")

def process_brokerage_transactions(brokerage_name, csv_files):
    """Process a single brokerage's transactions (for parallel execution)"""
    print(f"Processing {brokerage_name} transactions...")
    return process_csvs('transactions', csv_files, brokerage_name=brokerage_name)

def main():
    print("Starting optimized import process...")

    # Build dictionary of file lists
    csv_files = build_file_lists(FILEDIRS)

    # Process non-transaction CSVs (these are small, keep serial)
    print("Processing acquisitions...")
    acquisitions = process_csvs('acquisitions', csv_files['acquisitions'])

    print("Processing entities...")
    entities = process_csvs('entities', csv_files['entities'])

    print("Processing splits...")
    splits = process_csvs('splits', csv_files['splits'])

    # Process transaction CSVs in parallel
    print("\nProcessing transactions from all brokerages in parallel...")
    brokerages = [
        ('wallmine', csv_files['wallmine_transactions']),
        ('tdameritrade', csv_files['tdameritrade_transactions']),
        ('schwab', csv_files['schwab_transactions'])
    ]

    all_transactions = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all brokerage processing tasks
        futures = {
            executor.submit(process_brokerage_transactions, name, files): name
            for name, files in brokerages
        }

        # Collect results as they complete
        for future in as_completed(futures):
            brokerage_name = futures[future]
            try:
                transactions = future.result()
                all_transactions.extend(transactions)
                print(f"✓ Completed {brokerage_name}: {len(transactions)} transactions")
            except Exception as e:
                print(f"✗ Error processing {brokerage_name}: {e}")
                raise

    print(f"\nTotal transactions loaded: {len(all_transactions)}")

    # Validate and clean up transactions
    print("Validating transactions...")
    all_transactions = validate_transactions(all_transactions, entities)
    print("Cleaning up transactions...")
    all_transactions = cleanup_transactions(all_transactions)

    # Use a single database connection for all operations
    print("\nConnecting to database...")
    with MysqlDB(dbcfg) as db:
        # Create tables
        print("Creating tables...")
        db.execute(create_acquisitions_table_sql)
        db.execute(create_entities_table_sql)
        db.execute(create_splits_table_sql)
        db.execute(create_trades_table_sql)
        db.execute(create_dividends_table_sql)

        # Bulk insert all data
        print("\nBulk inserting data...")
        bulk_insert_acquisitions(db, acquisitions)
        bulk_insert_entities(db, entities)
        bulk_insert_splits(db, splits)
        bulk_insert_transactions(db, all_transactions)

        # Create indexes for faster queries
        print("\nCreating indexes...")
        for index_sql in transaction_table_indexes:
            db.create_index_safe(index_sql)
        print("✓ Indexes created")

        # Commit is handled automatically by context manager
        print("\nCommitting transaction...")

    # Every memoized read of trades/dividends/splits/entities/acquisitions is
    # now stale. mysql_query keys on the query string alone and cannot know
    # rows changed, so without this a freshly-imported trade stays invisible
    # for up to MYSQL_CACHE_TTL (4 hours) -- to summary validation, and to
    # daily_update.py, which would compute and PERSIST history from the
    # pre-import log.
    invalidate_query_cache()
    print("✓ Query cache invalidated")

    print("\n✓ Import completed successfully!")

if __name__ == "__main__":
    main()
