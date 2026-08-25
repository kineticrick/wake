"""
Layout assertions: the responsive properties are present and the desktop
values are unchanged.

These walk the component tree rather than rendering, so they need no browser
and no server. The browser-level acceptance check lives in
tests/libraries/test_mobile_viewport.py.
"""
import os
import unittest

os.environ['PORTFOLIO_DEMO_MODE'] = '1'   # no DB, no yfinance

import dash_mantine_components as dmc


def walk(component):
    """Yield every component in a Dash layout tree."""
    yield component
    children = getattr(component, 'children', None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, 'children') or hasattr(child, '_type'):
            yield from walk(child)


def grid_cols(layout):
    return [c for c in walk(layout) if type(c).__name__ == 'GridCol']


def graphs(layout):
    return [c for c in walk(layout) if type(c).__name__ == 'Graph']


class TestDimensionTabResponsive(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # tabs/__init__.py does `from .sectors_tab import sectors_tab`, so
        # this import already yields the Container -- the submodule name is
        # shadowed by the re-exported variable of the same name. There is no
        # `.sectors_tab` attribute on it.
        from visualization.dash.portfolio_dashboard.tabs import sectors_tab
        cls.layout = sectors_tab

    def test_every_gridcol_span_is_responsive(self):
        for col in grid_cols(self.layout):
            self.assertIsInstance(
                col.span, dict,
                f"span={col.span!r} is a fixed fraction at every width")

    def test_desktop_values_are_preserved(self):
        # Every responsive span must still name an md value -- that is what
        # keeps the laptop layout identical.
        for col in grid_cols(self.layout):
            self.assertIn('md', col.span)
            self.assertIn('base', col.span)

    def test_charts_have_no_fixed_pixel_height(self):
        for graph in graphs(self.layout):
            style = getattr(graph, 'style', None) or {}
            height = style.get('height', '')
            self.assertNotIn('px', str(height),
                             f"{graph.id} has a fixed pixel height")

    def test_table_has_both_desktop_and_mobile_views(self):
        boxes = [c for c in walk(self.layout) if type(c).__name__ == 'Box']
        self.assertTrue(any(getattr(b, 'visibleFrom', None) == 'md' for b in boxes))
        self.assertTrue(any(getattr(b, 'hiddenFrom', None) == 'md' for b in boxes))


class TestPortfolioTabResponsive(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # As with sectors_tab above: tabs/__init__.py does
        # `from .portfolio_tab import portfolio_tab`, so this import already
        # yields the Container -- there is no `.portfolio_tab` attribute on it.
        from visualization.dash.portfolio_dashboard.tabs import portfolio_tab
        cls.layout = portfolio_tab

    def test_every_gridcol_span_is_responsive(self):
        for col in grid_cols(self.layout):
            self.assertIsInstance(
                col.span, dict,
                f"span={col.span!r} is a fixed fraction at every width")

    def test_every_offset_collapses_on_mobile(self):
        # An offset on a full-width column just wastes a narrow screen.
        for col in grid_cols(self.layout):
            offset = getattr(col, 'offset', None)
            if offset is None:
                continue
            self.assertIsInstance(offset, dict)
            self.assertEqual(offset.get('base'), 0)

    def test_history_chart_has_no_fixed_pixel_height(self):
        history = [g for g in graphs(self.layout)
                   if g.id == 'portfolio-history-graph']
        self.assertEqual(len(history), 1)
        style = getattr(history[0], 'style', None) or {}
        self.assertNotIn('px', str(style.get('height', '')))

    def test_all_three_tables_have_a_mobile_card_view(self):
        boxes = [c for c in walk(self.layout) if type(c).__name__ == 'Box']
        card_ids = {getattr(b, 'id', None) for b in boxes
                    if getattr(b, 'hiddenFrom', None) == 'md'}
        self.assertIn('portfolio-milestones-table-cards', card_ids)
        self.assertIn('winners-table-cards', card_ids)
        self.assertIn('losers-table-cards', card_ids)


class TestRemainingTabsDoNotOverflow(unittest.TestCase):
    """Assets and Hypotheticals get responsive spans only -- no card view.
    They must not force horizontal scrolling, which fixed spans guarantee."""

    def _assert_all_spans_responsive(self, layout, name):
        for col in grid_cols(layout):
            self.assertIsInstance(
                col.span, dict,
                f"{name}: span={col.span!r} is fixed at every width")
            self.assertIn('base', col.span)
            self.assertIn('md', col.span)

    def test_assets_tab_spans_are_responsive(self):
        from visualization.dash.portfolio_dashboard.tabs import assets_tab
        self._assert_all_spans_responsive(assets_tab, 'assets')

    def test_hypotheticals_tab_spans_are_responsive(self):
        from visualization.dash.portfolio_dashboard.tabs import hypotheticals_tab
        self._assert_all_spans_responsive(hypotheticals_tab, 'hypotheticals')

    def test_chat_thread_height_is_viewport_relative(self):
        # Unlike assets_tab/hypotheticals_tab, chat_tab is NOT re-exported in
        # tabs/__init__.py, so this import yields the submodule -- the
        # layout Container is its `.chat_tab` attribute.
        from visualization.dash.portfolio_dashboard.tabs import chat_tab
        threads = [c for c in walk(chat_tab.chat_tab)
                   if getattr(c, 'id', None) == 'chat-thread']
        self.assertEqual(len(threads), 1)
        style = threads[0].style or {}
        combined = f"{style.get('minHeight', '')}{style.get('height', '')}"
        self.assertIn('vh', combined)


if __name__ == '__main__':
    unittest.main()
