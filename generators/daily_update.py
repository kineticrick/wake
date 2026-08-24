#!/usr/bin/env python
"""
Nightly derived-data updater.

Everything that touches yfinance or writes history lives here, so the Dash web
tier can run with PORTFOLIO_READ_ONLY=1 and never block a page load on a
58-73 second price fetch.

Idempotent: the handlers write with INSERT IGNORE / REPLACE INTO keyed on date,
so re-running after a partial failure is safe.

Usage:
    python generators/daily_update.py
    python generators/daily_update.py --verbose
"""
import argparse
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from libraries.db.history_meta import (gen_history_meta_table, start_run,
                                       finish_run, fail_run, table_max_dates)
from libraries.helpers import aggregate_assets_history_by_symbol
from libraries.HistoryHandlers import (AssetHistoryHandler,
                                       PortfolioHistoryHandler,
                                       AssetHypotheticalHistoryHandler,
                                       SectorHistoryHandler,
                                       AssetTypeHistoryHandler,
                                       AccountTypeHistoryHandler,
                                       GeographyHistoryHandler)

# read_only=False is passed EXPLICITLY everywhere below. Relying on the default
# would inherit PORTFOLIO_READ_ONLY from the environment, and a stray
# PORTFOLIO_READ_ONLY=1 would turn this job into a silent no-op.
#
# Stored as NAMES, not class objects: tests patch these classes onto this
# module (mock.patch.object(daily_update, 'SectorHistoryHandler', Fake)) at
# call time, and run_update() below looks each name up in this module's
# globals() when it runs -- so the patch takes effect. Freezing the classes
# themselves into a tuple at import time would capture the pre-patch
# originals and silently bypass any test double.
DIMENSION_HANDLER_NAMES = (
    'SectorHistoryHandler',
    'AssetTypeHistoryHandler',
    'AccountTypeHistoryHandler',
    'GeographyHistoryHandler',
)


def run_update(logger) -> dict:
    """
    Bring every derived history table up to date.

    Returns the per-table max dates after the run.
    """
    logger.info("Updating asset history...")
    ah = AssetHistoryHandler(read_only=False)

    # Portfolio and hypothetical history are both derived from asset history,
    # so they must run after it and consume its freshly-updated frame.
    assets_history_df = aggregate_assets_history_by_symbol(ah.history_df)

    logger.info("Updating portfolio history...")
    PortfolioHistoryHandler(assets_history_df=assets_history_df,
                            read_only=False)

    logger.info("Updating hypothetical history...")
    AssetHypotheticalHistoryHandler(assets_history_df=assets_history_df,
                                    read_only=False)

    for name in DIMENSION_HANDLER_NAMES:
        handler_class = globals()[name]
        logger.info("Updating %s...", name)
        handler_class(read_only=False)

    return table_max_dates()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verbose', action='store_true',
                        help='Log every handler as it runs')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('daily_update')

    gen_history_meta_table()
    run_id = start_run()

    try:
        tables = run_update(logger)
    except Exception as exc:                      # noqa: BLE001 - top-level job boundary
        logger.exception("Update failed; database keeps its last good data")
        fail_run(run_id, exc)
        return 1

    finish_run(run_id, tables)
    logger.info("Update complete: %s", tables)
    return 0


if __name__ == '__main__':
    sys.exit(main())
