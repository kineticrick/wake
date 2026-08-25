import unittest

import dash_mantine_components as dmc

from visualization.dash.portfolio_dashboard.components.responsive_table import (
    build_mobile_cards, responsive_table)


ROWS = [
    {'Sector': 'Technology', 'Current Value': 218655.69,
     'VW Return': 62.44, 'Cost Basis': 134856.56, 'Noise': 'ignore me'},
    {'Sector': 'Energy', 'Current Value': 8991.62,
     'VW Return': -10.17, 'Cost Basis': 10139.36, 'Noise': 'ignore me'},
]


class TestBuildMobileCards(unittest.TestCase):

    def test_one_card_per_row(self):
        cards = build_mobile_cards(ROWS, 'Sector',
                                   ['Current Value', 'VW Return'])
        self.assertEqual(len(cards), 2)

    def test_primary_field_is_the_heading(self):
        cards = build_mobile_cards(ROWS, 'Sector', ['Current Value'])
        heading = cards[0].children[0]
        self.assertEqual(heading.children, 'Technology')

    def test_only_requested_fields_appear(self):
        cards = build_mobile_cards(ROWS, 'Sector',
                                   ['Current Value', 'VW Return'])
        rendered = str(cards[0])
        self.assertIn('Current Value', rendered)
        self.assertIn('VW Return', rendered)
        # A field not asked for must not leak into the mobile view.
        self.assertNotIn('Noise', rendered)

    def test_empty_rows_give_empty_list(self):
        self.assertEqual(build_mobile_cards([], 'Sector', ['Current Value']), [])

    def test_missing_field_is_skipped_not_rendered_as_none(self):
        rows = [{'Sector': 'Technology'}]  # no 'Current Value' at all
        cards = build_mobile_cards(rows, 'Sector', ['Current Value'])
        self.assertEqual(len(cards), 1)          # card still renders
        self.assertNotIn('None', str(cards[0]))  # but no None leaks in

    def test_row_missing_the_primary_field_is_skipped(self):
        rows = [{'Current Value': 1.0}]
        self.assertEqual(build_mobile_cards(rows, 'Sector',
                                            ['Current Value']), [])

    def test_nan_value_is_skipped_not_rendered_as_string(self):
        rows = [{'Sector': 'Technology', 'Current Value': float('nan')}]
        cards = build_mobile_cards(rows, 'Sector', ['Current Value'])
        self.assertEqual(len(cards), 1)          # card still renders
        self.assertNotIn('nan', str(cards[0]))   # but NaN leaks not in

    def test_zero_value_is_rendered(self):
        rows = [{'Sector': 'Technology', 'Current Value': 0}]
        cards = build_mobile_cards(rows, 'Sector', ['Current Value'])
        rendered = str(cards[0])
        self.assertIn('Current Value', rendered)
        self.assertIn('0', rendered)             # 0 is real data

    def test_empty_string_value_is_rendered(self):
        rows = [{'Sector': 'Technology', 'Current Value': ''}]
        cards = build_mobile_cards(rows, 'Sector', ['Current Value'])
        rendered = str(cards[0])
        self.assertIn('Current Value', rendered)  # field shows even if value is empty


class TestResponsiveTable(unittest.TestCase):

    def test_emits_a_desktop_grid_and_a_mobile_card_container(self):
        box = responsive_table('sectors-table', 'Sector', ['Current Value'])
        desktop, mobile = box.children

        self.assertEqual(desktop.visibleFrom, 'md')
        self.assertEqual(mobile.hiddenFrom, 'md')

    def test_ids_follow_the_documented_convention(self):
        box = responsive_table('sectors-table', 'Sector', ['Current Value'])
        desktop, mobile = box.children

        self.assertEqual(desktop.children.id, 'sectors-table')
        self.assertEqual(mobile.id, 'sectors-table-cards')

    def test_grid_kwargs_reach_the_aggrid(self):
        box = responsive_table('sectors-table', 'Sector', ['Current Value'],
                               dashGridOptions={"domLayout": "autoHeight"})
        grid = box.children[0].children
        self.assertEqual(grid.dashGridOptions, {"domLayout": "autoHeight"})


if __name__ == '__main__':
    unittest.main()
