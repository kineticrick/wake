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


if __name__ == '__main__':
    unittest.main()
