"""
Regression guard: every mobile-card field name actually exists in the frame
it will be applied to.

build_mobile_cards() (visualization/dash/portfolio_dashboard/components/
responsive_table.py) deliberately skips any field it cannot find in a row --
that is how it avoids rendering "None"/"nan" for a genuinely absent value.
That means a wrong or stale field name in one of the *_MOBILE_FIELDS lists
does not raise or fail loudly: it silently renders a blank line on the phone
card. Nothing else in the suite catches this -- test_responsive_layout.py
checks component-tree structure (spans, card containers present) and
test_responsive_table.py exercises build_mobile_cards() with synthetic rows,
not the real column names. This module checks content instead of structure,
which is why it is a separate file from test_responsive_layout.py rather than
folded into it.

Two spellings of "percent of total portfolio" already coexist in this
codebase ('% of Total Portfolio' on the four dimension summary frames vs.
'% Total Portfolio' on current_portfolio_summary_df) -- exactly the kind of
drift this guards against.

Uses demo mode (PORTFOLIO_DEMO_MODE=1) so it needs no live DB/yfinance; demo
data columns are produced by the same shared code paths (_gen_summary_df,
get_ranked_assets) as the real handler, so they match.
"""
import os
import unittest

os.environ['PORTFOLIO_DEMO_MODE'] = '1'   # no DB, no yfinance

from visualization.dash.portfolio_dashboard.globals import (
    DASH_HANDLER, PORTFOLIO_MILESTONES)
from visualization.dash.portfolio_dashboard.tabs.dimension_tab_factory import (
    DIMENSION_MOBILE_FIELDS)
from visualization.dash.portfolio_dashboard.tabs.portfolio_tab import (
    MILESTONE_MOBILE_FIELDS, MOVERS_MOBILE_FIELDS)


# One summary_df attribute per dimension tab (sectors, asset types, account
# types, geography) -- all four are built by create_dimension_tab() with the
# same DIMENSION_MOBILE_FIELDS list, so all four must be checked.
DIMENSION_SUMMARY_ATTRS = [
    'sectors_summary_df',
    'asset_types_summary_df',
    'account_types_summary_df',
    'geography_summary_df',
]


class TestMobileFieldsMatchColumns(unittest.TestCase):

    def test_dimension_tabs_mobile_fields_exist_in_their_summary_df(self):
        for attr in DIMENSION_SUMMARY_ATTRS:
            summary_df = getattr(DASH_HANDLER, attr)
            missing = [f for f in DIMENSION_MOBILE_FIELDS
                       if f not in summary_df.columns]
            self.assertEqual(
                missing, [],
                f"{attr}: mobile field(s) {missing} not in columns "
                f"{list(summary_df.columns)}")

    def test_milestone_mobile_fields_exist_in_portfolio_milestones(self):
        missing = [f for f in MILESTONE_MOBILE_FIELDS
                   if f not in PORTFOLIO_MILESTONES.columns]
        self.assertEqual(
            missing, [],
            f"PORTFOLIO_MILESTONES: mobile field(s) {missing} not in "
            f"columns {list(PORTFOLIO_MILESTONES.columns)}")

    def test_movers_mobile_fields_exist_in_ranked_assets(self):
        ranked_df = DASH_HANDLER.get_ranked_assets('1d', 'price', count=5)
        missing = [f for f in MOVERS_MOBILE_FIELDS
                   if f not in ranked_df.columns]
        self.assertEqual(
            missing, [],
            f"get_ranked_assets() output: mobile field(s) {missing} not in "
            f"columns {list(ranked_df.columns)}")


if __name__ == '__main__':
    unittest.main()
