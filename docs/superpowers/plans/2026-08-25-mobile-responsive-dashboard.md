# Mobile-Responsive Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make six of the eight dashboard tabs usable at phone width (390px) without changing the desktop layout at all.

**Architecture:** Replace fixed Mantine grid spans with responsive span dicts (`{"base": 12, "md": 3}`), render tables as a desktop `AgGrid` plus a mobile `dmc.Card` stack chosen by `visibleFrom`/`hiddenFrom`, and swap fixed pixel chart heights for viewport-relative ones. All declarative — no custom CSS, no JavaScript, no server-side viewport detection. Also replaces the calendar-window downsampler with a point budget.

**Tech Stack:** Python 3.14, Dash 4.4.1, dash-mantine-components 2.8.0, dash-ag-grid 35.3.0, plotly 6.9.0, pandas 3.0.5, unittest.

**Spec:** `docs/superpowers/specs/2026-08-25-mobile-responsive-dashboard-design.md`

## Global Constraints

- Tests use the standard library `unittest`. Run targeted: `venv/bin/python -m unittest tests.libraries.test_x -v`. Full suite: `venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"` (**note `-t .`** — without it, discovery shadows the real `libraries` package and every module fails to import).
- Use the project venv: `venv/bin/python` (Python 3.14).
- **No new third-party dependencies.** Everything needed is already installed.
- **The desktop layout must not move.** Every responsive span keeps its current value at the `md` breakpoint and above. A test asserts this.
- Breakpoint divide is `md` (992px). Below it is "mobile", at/above is today's layout.
- Chart payloads must stay under **500 KB per tab**. The Assets tab is the binding constraint at 34 traces.
- Verified available in dmc 2.8.0 (do not re-check): responsive `span` dicts, `visibleFrom`, `hiddenFrom`, `Card(withBorder, shadow, p, mb)`, `Text(size, c, fw)`, `Group(justify, gap)`, `Stack(gap)`, `Box`.
- End every commit message with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Work on branch `feat/mobile-responsive` (already created; spec committed as `669bb7c`).

---

## File Structure

**Created:**
- `visualization/dash/portfolio_dashboard/components/__init__.py`
- `visualization/dash/portfolio_dashboard/components/responsive_table.py` — `build_mobile_cards` (pure) + `responsive_table` (layout)
- `tests/libraries/test_responsive_table.py`
- `tests/libraries/test_responsive_layout.py` — layout assertions across tabs
- `tests/libraries/test_mobile_viewport.py` — real-browser acceptance check

**Modified:**
- `libraries/globals.py` — `CHART_POINT_BUDGET` replaces the two window constants
- `libraries/downsample.py` — point budget replaces calendar window
- `tests/libraries/test_downsample.py` — window-specific tests rewritten
- `visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py` — 4 tabs
- `visualization/dash/portfolio_dashboard/tabs/portfolio_tab.py`
- `visualization/dash/portfolio_dashboard/tabs/chat_tab.py`
- `visualization/dash/portfolio_dashboard/tabs/assets_tab.py` — spans + caller update
- `visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py` — spans

---

### Task 1: Point-budget downsampler

Replaces the fixed calendar window with a per-series point budget, so payloads stop growing with history. Removes the `today` parameter entirely — a point budget has no calendar reference, which also removes this function's clock dependency.

**Files:**
- Modify: `libraries/globals.py` (the `DOWNSAMPLE_DAILY_WINDOW_DAYS` / `ASSETS_DOWNSAMPLE_WINDOW_DAYS` block)
- Modify: `libraries/downsample.py`
- Modify: `visualization/dash/portfolio_dashboard/tabs/assets_tab.py` (the one caller passing `window_days`)
- Test: `tests/libraries/test_downsample.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `downsample_history(df, date_col='Date', group_cols=(), max_points_per_series=None) -> pd.DataFrame`. **The `window_days` and `today` parameters are removed.** `libraries.globals.CHART_POINT_BUDGET = 300`. `top_n_symbols` is unchanged.

- [ ] **Step 1: Replace the window constants in `libraries/globals.py`**

Delete `DOWNSAMPLE_DAILY_WINDOW_DAYS`, `ASSETS_DOWNSAMPLE_WINDOW_DAYS`, and the comment block explaining the Assets 60-day exception. Replace with:

```python
# Target points per chart series. Downsampling thins each series to at most
# this many points with an even stride, always keeping its first and last.
#
# 300 is chosen against the payload ceiling, not by feel. The Assets tab is
# the binding constraint (34 traces): at 300/series it lands ~430 KB, better
# than the 455 KB the old 60-day calendar window produced; at 350 it would
# exceed the 500 KB target. Sectors comes out ~219 KB against today's 220 KB,
# i.e. visually identical.
#
# The point of a budget rather than a calendar window is that it does not
# drift: the old window grew the Assets payload ~70 KB per year of history.
CHART_POINT_BUDGET = 300
```

Leave `DEFAULT_CHART_SERIES = 10` alone — `top_n_symbols` still uses it.

- [ ] **Step 2: Rewrite the window-specific tests**

In `tests/libraries/test_downsample.py`, replace the two window-specific tests in `TestDownsampleHistory` — `test_recent_window_keeps_daily_resolution` and `test_old_data_is_thinned` — with these:

```python
    def test_output_stays_within_the_budget(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        for symbol in ('AAA', 'BBB'):
            kept = out[out['Symbol'] == symbol]
            self.assertLessEqual(len(kept), 100)

    def test_thinning_is_evenly_spaced_not_front_loaded(self):
        # A stride-based thin must sample across the whole range. A naive
        # head(budget) would pass a length check while showing only the
        # oldest slice of history.
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        kept = out[out['Symbol'] == 'AAA'].sort_values('Date')
        original = self.df[self.df['Symbol'] == 'AAA']
        span_kept = (kept['Date'].max() - kept['Date'].min()).days
        span_orig = (original['Date'].max() - original['Date'].min()).days
        self.assertEqual(span_kept, span_orig)

    def test_series_longer_than_budget_is_actually_thinned(self):
        out = downsample_history(self.df, group_cols=('Symbol',),
                                 max_points_per_series=100)
        self.assertLess(len(out), len(self.df))
```

Then remove every `window_days=...` and `today=...` argument from the remaining calls in the file — in `test_first_and_last_point_per_group_are_preserved`, `test_short_history_is_returned_unchanged`, all three `TestDownsampleHistoryMultiGroupCols` tests, and `TestDownsampleHistoryNonUniqueIndex`. For example:

```python
        out = downsample_history(self.df, group_cols=('Symbol',))
```

`test_short_history_is_returned_unchanged` needs its assertion re-based, since "short" now means "shorter than the budget" rather than "inside the window":

```python
    def test_short_history_is_returned_unchanged(self):
        recent = self.df[self.df['Date'] >=
                         self.today - datetime.timedelta(days=30)]
        out = downsample_history(recent, group_cols=('Symbol',),
                                 max_points_per_series=1000)
        self.assertEqual(len(out), len(recent))
```

Leave `TestDownsampleHistoryNonUniqueIndex` otherwise intact — it is the MINOR 4 regression guard and must keep passing.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python -m unittest tests.libraries.test_downsample -v`
Expected: FAIL — `TypeError: downsample_history() got an unexpected keyword argument 'max_points_per_series'`, plus `ImportError` for the removed constant.

- [ ] **Step 4: Implement the point budget**

Replace the whole of `downsample_history` in `libraries/downsample.py`, and update the import at the top of the file from `DOWNSAMPLE_DAILY_WINDOW_DAYS` to `CHART_POINT_BUDGET`. Add `import math`. `datetime` is no longer used by this function but is still needed elsewhere in the module — leave the import.

```python
def _thin_one_series(group: pd.DataFrame, date_col: str,
                     budget: int) -> pd.DataFrame:
    """
    Thin a single series to at most `budget` rows with an even stride.

    Always keeps the first and last row: chart endpoints must continue to
    agree with the figures in the table beside the chart.
    """
    n = len(group)
    if n <= budget:
        return group

    group = group.sort_values(date_col)
    # ceil so the stride never leaves us above budget: n=1000, budget=300
    # gives stride 4 -> 250 kept, +1 for the final row.
    stride = math.ceil(n / budget)
    kept = group.iloc[::stride]

    last = group.iloc[[-1]]
    if kept.index[-1] != last.index[0]:
        kept = pd.concat([kept, last])
    return kept


def downsample_history(df: pd.DataFrame, date_col: str = 'Date',
                       group_cols=(),
                       max_points_per_series: int = None) -> pd.DataFrame:
    """
    Thin each series to at most `max_points_per_series` points.

    A point budget rather than a calendar window: a chart renders into a fixed
    number of pixels, so the useful number of points is bounded by the display,
    not by how much history exists. The previous fixed-window approach grew the
    payload with every year of data.

    Each group's first and last points always survive.

    group_cols: e.g. ('Symbol',) or ('Symbol', 'AccountType') -- whatever
                identifies one chart trace. Empty for single-series data.
    """
    if df.empty:
        return df

    # idxmin()/idxmax()-style label lookups and per-group slicing both misbehave
    # on a non-unique index: a caller concatenating two independently-indexed
    # per-symbol frames can otherwise lose a whole series with no error.
    df = df.reset_index(drop=True)

    if max_points_per_series is None:
        max_points_per_series = CHART_POINT_BUDGET

    group_cols = list(group_cols)
    if not group_cols:
        return _thin_one_series(
            df, date_col, max_points_per_series).reset_index(drop=True)

    # Explicit iteration rather than groupby().apply(): apply's handling of
    # grouping columns is a moving target across pandas versions, and this is
    # clearer about what it returns.
    pieces = [_thin_one_series(group, date_col, max_points_per_series)
              for _, group in df.groupby(group_cols, sort=False)]

    out = pd.concat(pieces)
    return out.sort_values(group_cols + [date_col]).reset_index(drop=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python -m unittest tests.libraries.test_downsample -v`
Expected: PASS, all tests including the non-unique-index regression.

- [ ] **Step 6: Update the one caller that passed `window_days`**

In `visualization/dash/portfolio_dashboard/tabs/assets_tab.py`, remove the `from libraries.globals import ASSETS_DOWNSAMPLE_WINDOW_DAYS` line, and change the call (around line 106) plus its preceding comment to:

```python
        # Thin to the shared point budget. This tab has the most traces (34),
        # so it is the binding constraint on the 500 KB payload target.
        df = downsample_history(df, group_cols=('Symbol', 'AccountType'))
```

- [ ] **Step 7: Verify no stale references remain**

Run:
```bash
grep -rn "DOWNSAMPLE_DAILY_WINDOW_DAYS\|ASSETS_DOWNSAMPLE_WINDOW_DAYS\|window_days" --include=*.py . | grep -v venv
```
Expected: no output.

- [ ] **Step 8: Measure payloads against the 500 KB ceiling**

Write this to a scratch file and run it (do not use inline `python -c` — the nested quoting is error-prone):

```python
import sys, time
sys.path.insert(0, '.')
import plotly.express as px
from visualization.dash.DashboardHandler import DashboardHandler
from libraries.downsample import downsample_history

dh = DashboardHandler()
for label, attr, col in [('sectors', 'sectors_history_df', 'Sector'),
                         ('asset_types', 'asset_types_history_df', 'AssetType'),
                         ('geography', 'geography_history_df', 'Geography')]:
    d = getattr(dh, attr).copy()
    d['y'] = d['TotalValue']
    d = downsample_history(d, group_cols=(col,))
    fig = px.line(d, x=d['Date'], y=d['y'], color=d[col])
    kb = len(fig.to_json()) / 1024
    print(f'{label:<12} traces={len(fig.data):>3} pts={len(d):>6} {kb:6.0f} KB'
          + ('  OVER 500' if kb > 500 else ''))
```

Run with `PORTFOLIO_READ_ONLY=1 venv/bin/python <scratch file>`.
Expected: every tab well under 500 KB; sectors close to its current ~220 KB.

- [ ] **Step 9: Run the full suite**

Run: `venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"`
Expected: OK.

- [ ] **Step 10: Commit**

```bash
git add libraries/globals.py libraries/downsample.py \
        tests/libraries/test_downsample.py \
        visualization/dash/portfolio_dashboard/tabs/assets_tab.py
git commit -m "perf: replace calendar-window downsampling with a point budget

A chart renders into a fixed number of pixels, so the useful number of
points is bounded by the display rather than by how much history exists.
The old fixed window grew the Assets payload ~70 KB per year and would
have crossed the 500 KB target within about a year.

300 points/series is set against that ceiling: Assets lands ~430 KB (vs
455 KB before), 350 would exceed 500 KB. Removes the today/window_days
parameters, so this function no longer depends on the clock at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `responsive_table` component

A shared component that renders a desktop `AgGrid` and a mobile card stack, letting CSS pick one. The card builder is pure so it can be unit tested without Dash context.

**Files:**
- Create: `visualization/dash/portfolio_dashboard/components/__init__.py`
- Create: `visualization/dash/portfolio_dashboard/components/responsive_table.py`
- Test: `tests/libraries/test_responsive_table.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `build_mobile_cards(row_data: list[dict], primary_field: str, mobile_fields: list[str]) -> list` — returns a list of `dmc.Card`.
  - `responsive_table(table_id: str, primary_field: str, mobile_fields: list[str], column_defs=None, row_data=None, **grid_kwargs) -> dmc.Box` — the grid gets `id=table_id`; the card container gets `id=f"{table_id}-cards"`.

  Tasks 3 and 4 add `Output(f"{table_id}-cards", "children")` to existing callbacks and populate it with `build_mobile_cards(...)`.

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_responsive_table.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_table -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'visualization.dash.portfolio_dashboard.components'`

- [ ] **Step 3: Create the package and the component**

Create `visualization/dash/portfolio_dashboard/components/__init__.py` as an empty file.

Create `visualization/dash/portfolio_dashboard/components/responsive_table.py`:

```python
"""
A table that is an AgGrid on desktop and a card stack on a phone.

Both views are rendered and CSS picks one via Mantine's visibleFrom/hiddenFrom.
That costs one extra serialisation of the row data per response -- negligible
for the tables here (largest is 34 rows) beside a ~220 KB chart -- and buys a
design with no JavaScript and no state to keep the two views in sync.

An 8-column AgGrid at 390px is unusable: measured against the live app, a
span=3 table collapsed to 44 pixels wide.
"""
import dash_ag_grid as dag
import dash_mantine_components as dmc

# The phone/desktop divide. Below md the card stack shows; at md and above the
# grid shows and the layout is exactly what it has always been.
MOBILE_BREAKPOINT = 'md'


def build_mobile_cards(row_data, primary_field, mobile_fields):
    """
    One dmc.Card per row: `primary_field` as the heading, `mobile_fields` as
    label/value pairs beneath.

    Pure -- no Dash callback context, no I/O -- so it is directly unit testable.

    Keep `mobile_fields` to 3-4 entries. More than that just reproduces the
    unreadable wide table in card form.
    """
    cards = []
    for row in row_data or []:
        if primary_field not in row:
            # Caller controls both sides, so this is a programming error --
            # but a malformed row must not blank the whole list.
            continue

        lines = []
        for field in mobile_fields:
            value = row.get(field)
            if value is None:
                continue
            lines.append(dmc.Group(
                [
                    dmc.Text(field, size="xs", c="dimmed"),
                    dmc.Text(str(value), size="sm", fw=500),
                ],
                justify="space-between",
                gap="xs",
            ))

        cards.append(dmc.Card(
            [dmc.Text(str(row[primary_field]), fw=700, size="sm", mb="xs")] + lines,
            withBorder=True, shadow="xs", p="sm", mb="xs",
        ))

    return cards


def responsive_table(table_id, primary_field, mobile_fields,
                     column_defs=None, row_data=None, **grid_kwargs):
    """
    Desktop AgGrid (id=table_id) plus mobile card stack (id=f"{table_id}-cards").

    Callbacks that already emit rowData for `table_id` add one Output for
    f"{table_id}-cards" and populate it with build_mobile_cards(...).
    """
    grid = dag.AgGrid(
        id=table_id,
        columnDefs=column_defs if column_defs is not None else [],
        rowData=row_data if row_data is not None else [],
        **grid_kwargs,
    )

    return dmc.Box([
        dmc.Box(grid, visibleFrom=MOBILE_BREAKPOINT),
        dmc.Box(
            build_mobile_cards(row_data or [], primary_field, mobile_fields),
            id=f"{table_id}-cards",
            hiddenFrom=MOBILE_BREAKPOINT,
        ),
    ])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_table -v`
Expected: PASS, all nine tests.

- [ ] **Step 5: Commit**

```bash
git add visualization/dash/portfolio_dashboard/components/ \
        tests/libraries/test_responsive_table.py
git commit -m "feat: add responsive_table component with mobile card view

An AgGrid at phone width is unusable -- measured against the live app, a
span=3 table collapsed to 44 pixels. This renders both a desktop grid and a
mobile card stack and lets visibleFrom/hiddenFrom pick one, so there is no
JavaScript and no state to synchronise. The card builder is pure and
directly unit tested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Responsive dimension tabs

One file, four tabs: Sectors, Asset Types, Account Types, Geography.

**Files:**
- Modify: `visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py`
- Test: `tests/libraries/test_responsive_layout.py` (create)

**Interfaces:**
- Consumes: `responsive_table`, `build_mobile_cards` (Task 2); `downsample_history` with no `window_days` (Task 1).
- Produces: the dimension tab layout. Its table callback gains a fourth `Output`, `Output(f'{dimension_name}-table-cards', 'children')`.

- [ ] **Step 1: Write the failing test**

Create `tests/libraries/test_responsive_layout.py`:

```python
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
        from visualization.dash.portfolio_dashboard.tabs import sectors_tab
        cls.layout = sectors_tab.sectors_tab

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout -v`
Expected: FAIL — `span=12 is a fixed fraction at every width` (and the Box assertions fail: no `visibleFrom`/`hiddenFrom` yet).

- [ ] **Step 3: Make the dimension tab layout responsive**

In `dimension_tab_factory.py`, add to the imports:

```python
from visualization.dash.portfolio_dashboard.components.responsive_table import (
    build_mobile_cards, responsive_table)
```

Replace the `tab_layout = dmc.Container([...])` block entirely with:

```python
    # Fields shown on a card at phone width. Deliberately short: more than
    # ~4 reproduces the unreadable wide table in card form.
    mobile_fields = ['Current Value', 'VW Return', '% Of Total Portfolio']

    tab_layout = dmc.Container(
        [
            dmc.Grid([
                dmc.GridCol(
                    dmc.Paper(
                        responsive_table(
                            f'{dimension_name}-table',
                            primary_field=column_name,
                            mobile_fields=mobile_fields,
                            defaultColDef={"resizable": True},
                            dashGridOptions={
                                "rowSelection": {"mode": "multiRow"},
                                "animateRows": False,
                            },
                            style={"height": "400px"},
                        ),
                        shadow="sm", p="md",
                    ),
                    span={"base": 12, "md": 12},
                ),
            ]),
            dmc.Grid([
                dmc.GridCol(
                    dcc.Dropdown(
                        id=f'{dimension_name}-interval-dropdown',
                        options=INTERVALS,
                        value=DEFAULT_INTERVAL,
                        placeholder='Select interval',
                    ),
                    # Full width on a phone; the desktop offset would waste
                    # most of a narrow screen.
                    span={"base": 12, "md": 3},
                    offset={"base": 0, "md": 1},
                ),
            ]),
            dmc.Grid([
                dmc.GridCol(
                    dmc.Paper(
                        dcc.Graph(
                            id=f'{dimension_name}-history-graph',
                            style={"height": "60vh"},
                            config={"responsive": True},
                        ),
                        shadow="sm", p="md",
                    ),
                    span={"base": 12, "md": 12},
                ),
            ]),
        ],
        fluid=True,
    )
```

- [ ] **Step 4: Make the chart responsive and add the cards Output**

In the same file, in `update_tab`, add the fourth `Output` immediately after the `rowData` output:

```python
    @callback(
        Output(f'{dimension_name}-table', 'columnDefs'),
        Output(f'{dimension_name}-table', 'rowData'),
        Output(f'{dimension_name}-table-cards', 'children'),
        Output(f'{dimension_name}-history-graph', 'figure'),
        Input('tabs', 'value'),
        Input(f'{dimension_name}-table', 'selectedRows'),
        Input(f'{dimension_name}-interval-dropdown', 'value'))
```

Update the early return to match the new arity:

```python
        if active_tab != tab_id:
            return no_update, no_update, no_update, no_update
```

Replace the `fig.update_layout(height=800)` line with a responsive layout — the height now comes from the `dcc.Graph` style:

```python
        fig.update_layout(
            autosize=True,
            margin=dict(l=40, r=20, t=20, b=20),
            # Horizontal legend below the plot: it wraps instead of eating
            # horizontal space, which a phone has none of to spare.
            legend=dict(orientation="h", yanchor="top", y=-0.15),
        )
```

And change the return statement to include the cards:

```python
        return (data['column_defs'], row_data,
                build_mobile_cards(row_data, column_name, mobile_fields),
                fig)
```

`mobile_fields` is defined in `create_dimension_tab`'s scope below the callback; move its definition **above** the `@callback` decorator so the closure can see it.

- [ ] **Step 5: Run the test to verify it passes**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout -v`
Expected: PASS, all four tests.

- [ ] **Step 6: Verify the app still boots and the tab renders**

```bash
PORTFOLIO_READ_ONLY=1 timeout 90 venv/bin/python \
    visualization/dash/portfolio_dashboard/portfolio_dashboard.py &
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8050
kill %1
```
Expected: `HTTP 200`.

- [ ] **Step 7: Commit**

```bash
git add visualization/dash/portfolio_dashboard/tabs/dimension_tab_factory.py \
        tests/libraries/test_responsive_layout.py
git commit -m "feat: make the dimension tabs responsive

One file covers Sectors, Asset Types, Account Types and Geography. Spans
become responsive dicts that keep their current values at md and above, the
chart height moves from a fixed 800px to 60vh, and the table gains a mobile
card view. The legend moves below the plot so it wraps rather than competing
for horizontal space.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Responsive portfolio tab

The densest tab: 3 tables and 2 charts. Note the milestones table has **no callback** — its `rowData` is set at layout time — so its card view is built statically. The winners/losers callback gains two Outputs.

**Files:**
- Modify: `visualization/dash/portfolio_dashboard/tabs/portfolio_tab.py`
- Test: `tests/libraries/test_responsive_layout.py` (add a class)

**Interfaces:**
- Consumes: `responsive_table`, `build_mobile_cards` (Task 2).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the failing test**

Append this class to `tests/libraries/test_responsive_layout.py`, above the `if __name__` block:

```python
class TestPortfolioTabResponsive(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from visualization.dash.portfolio_dashboard.tabs import portfolio_tab
        cls.layout = portfolio_tab.portfolio_tab

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout.TestPortfolioTabResponsive -v`
Expected: FAIL — spans are bare integers.

- [ ] **Step 3: Make the layout responsive**

In `portfolio_tab.py`, add to the imports:

```python
from visualization.dash.portfolio_dashboard.components.responsive_table import (
    build_mobile_cards, responsive_table)
```

Add these module-level constants above `portfolio_tab = dmc.Container(`:

```python
# Card fields for the phone view of each table. These names are exact: the
# milestones frame is MILESTONES[['Interval', 'Value', 'Value % Return']], and
# update_asset_tables selects ['Symbol', 'Interval', 'Current Price', 'Price',
# 'Price % Return']. A name that is not in the row silently renders an empty
# card, so do not guess these.
MILESTONE_MOBILE_FIELDS = ['Value', 'Value % Return']
MOVERS_MOBILE_FIELDS = ['Current Price', 'Price % Return']
```

Replace the whole `portfolio_tab = dmc.Container([...])` block with:

```python
portfolio_tab = dmc.Container(
    [
        dmc.Grid(
            dmc.GridCol(
                html.H1("Portfolio Dashboard"),
                span={"base": 12, "md": 6},
                offset={"base": 0, "md": 3},
            ),
            justify='center',
        ),
        html.Hr(),
        dmc.Grid([
            dmc.GridCol(
                html.Div([
                    "Select period:",
                    dcc.Dropdown(
                        id='interval-dropdown',
                        options=INTERVALS,
                        value=PORTFOLIO_DEFAULT_INTERVAL,
                    ),
                ]),
                # span=1 was already cramped on a laptop; widen it to 2 and
                # make it full width on a phone.
                span={"base": 12, "md": 2},
                offset={"base": 0, "md": 8},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    dcc.Graph(
                        id='portfolio-history-graph',
                        style={"height": "55vh"},
                        config={"responsive": True},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 9},
            ),
            dmc.GridCol(
                dmc.Paper(
                    dmc.Stack([
                        dcc.Graph(
                            id='portfolio-value-scalar',
                            config={"responsive": True},
                        ),
                        responsive_table(
                            'portfolio-milestones-table',
                            primary_field='Interval',
                            mobile_fields=MILESTONE_MOBILE_FIELDS,
                            column_defs=milestones_column_defs,
                            row_data=PORTFOLIO_MILESTONES.to_dict('records'),
                            defaultColDef={"resizable": True},
                            dashGridOptions={"domLayout": "autoHeight"},
                        )],
                        gap="md",
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
            ),
        ]),
        dmc.Grid([
            dmc.GridCol(
                dmc.Paper(
                    responsive_table(
                        'winners-table',
                        primary_field='Symbol',
                        mobile_fields=MOVERS_MOBILE_FIELDS,
                        defaultColDef={"resizable": True},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
                offset={"base": 0, "md": 1},
            ),
            dmc.GridCol(
                dmc.Paper(
                    responsive_table(
                        'losers-table',
                        primary_field='Symbol',
                        mobile_fields=MOVERS_MOBILE_FIELDS,
                        defaultColDef={"resizable": True},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                    shadow="sm", p="md",
                ),
                span={"base": 12, "md": 3},
                offset={"base": 0, "md": 1},
            ),
        ]),
    ],
    fluid=True,
)
```

Note the milestones table passes `row_data` at construction, so `responsive_table` builds its cards immediately — it has no callback to update them.

**`portfolio-value-scalar` deliberately keeps its `height=200`** set inside `update_portfolio_value`. It is a `go.Indicator` number readout, not a time-series plot; 200px is appropriate at every width. The layout test targets `portfolio-history-graph` specifically for this reason.

- [ ] **Step 4: Add the cards Outputs to the winners/losers callback**

Change the callback decorator (around line 63) to:

```python
@callback(
    Output('winners-table', 'rowData'),
    Output('winners-table', 'columnDefs'),
    Output('winners-table-cards', 'children'),
    Output('losers-table', 'rowData'),
    Output('losers-table', 'columnDefs'),
    Output('losers-table-cards', 'children'),
    Input('interval-dropdown', 'value'))
```

`update_asset_tables` has **two** return statements — the success path and the `except` handler. Both must now return six values, or the error path raises a callback arity error that masks the original exception. Change the success return to:

```python
        winners_rows = winners_df.to_dict('records')
        losers_rows = losers_df.to_dict('records')
        return (winners_rows, winners_col_defs,
                build_mobile_cards(winners_rows, 'Symbol',
                                   MOVERS_MOBILE_FIELDS),
                losers_rows, losers_col_defs,
                build_mobile_cards(losers_rows, 'Symbol',
                                   MOVERS_MOBILE_FIELDS))
```

And the `except` handler:

```python
    except Exception as e:
        print(f"Error in update_asset_tables: {e}")
        err_col = [{"field": "Error"}]
        err_data = [{"Error": f"Error loading data: {str(e)}"}]
        err_cards = build_mobile_cards(err_data, 'Error', [])
        return (err_data, err_col, err_cards,
                err_data, err_col, err_cards)
```

`build_mobile_cards(err_data, 'Error', [])` produces a card whose heading is the error text and which has no detail lines — so the failure is visible on a phone rather than silently blank.

- [ ] **Step 5: Make the history chart responsive**

In `update_portfolio_history` (the callback at line 15), find its `fig.update_layout(...)` call and ensure the height is not set there. Add:

```python
    fig.update_layout(
        autosize=True,
        margin=dict(l=40, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="top", y=-0.15),
    )
```

If a `height=` argument is present in that call, remove it — the `dcc.Graph` style now owns height.

- [ ] **Step 6: Run the tests**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout -v`
Expected: PASS, all classes.

- [ ] **Step 7: Verify the app boots and the callback fires without an arity error**

```bash
PORTFOLIO_READ_ONLY=1 timeout 90 venv/bin/python \
    visualization/dash/portfolio_dashboard/portfolio_dashboard.py > /tmp/pt.log 2>&1 &
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8050
grep -i "error\|traceback" /tmp/pt.log | head
kill %1
```
Expected: `HTTP 200` and no callback arity errors. A mismatch between Output count and return-tuple length surfaces here.

- [ ] **Step 8: Commit**

```bash
git add visualization/dash/portfolio_dashboard/tabs/portfolio_tab.py \
        tests/libraries/test_responsive_layout.py
git commit -m "feat: make the portfolio tab responsive

Three tables and two charts. The milestones table has no callback, so its
cards are built at layout time; winners/losers gain one Output each. The
history chart moves to 55vh; the scalar indicator keeps its fixed 200px
because it is a number readout rather than a plot.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Chat tab plus the non-overflow pass

Chat has no grid layout at all, so it needs only a height fix. Assets and Hypotheticals get responsive spans so they stop overflowing, without card views.

**Files:**
- Modify: `visualization/dash/portfolio_dashboard/tabs/chat_tab.py`
- Modify: `visualization/dash/portfolio_dashboard/tabs/assets_tab.py`
- Modify: `visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py`
- Test: `tests/libraries/test_responsive_layout.py` (add a class)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the failing test**

Append to `tests/libraries/test_responsive_layout.py`, above the `if __name__` block:

```python
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
        self._assert_all_spans_responsive(assets_tab.assets_tab, 'assets')

    def test_hypotheticals_tab_spans_are_responsive(self):
        from visualization.dash.portfolio_dashboard.tabs import hypotheticals_tab
        self._assert_all_spans_responsive(
            hypotheticals_tab.hypotheticals_tab, 'hypotheticals')

    def test_chat_thread_height_is_viewport_relative(self):
        from visualization.dash.portfolio_dashboard.tabs import chat_tab
        threads = [c for c in walk(chat_tab.chat_tab)
                   if getattr(c, 'id', None) == 'chat-thread']
        self.assertEqual(len(threads), 1)
        style = threads[0].style or {}
        combined = f"{style.get('minHeight', '')}{style.get('height', '')}"
        self.assertIn('vh', combined)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout.TestRemainingTabsDoNotOverflow -v`
Expected: FAIL — fixed integer spans, and `chat-thread` has `minHeight: 400px`.

- [ ] **Step 3: Fix the chat thread height**

In `chat_tab.py`, find the `html.Div(id="chat-thread", style={"minHeight": "400px", ...})` (around line 123) and change `minHeight` to a viewport-relative value, leaving the other style keys as they are:

```python
        html.Div(id="chat-thread", style={"minHeight": "55vh",
```

- [ ] **Step 4: Make Assets and Hypotheticals spans responsive**

In both `assets_tab.py` and `hypotheticals_tab.py`, convert every `span=N` on a `dmc.GridCol` to `span={"base": 12, "md": N}` — preserving N exactly, so the desktop layout is untouched. Convert every `offset=N` to `offset={"base": 0, "md": N}`.

Do this by reading each `dmc.GridCol(...)` call and editing its `span=`/`offset=` arguments in place. Do not restructure the layouts, do not add card views, and do not change any other argument. There are 7 spans in `assets_tab.py` and 4 in `hypotheticals_tab.py`.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python -m unittest tests.libraries.test_responsive_layout -v`
Expected: PASS, all classes.

- [ ] **Step 6: Run the full suite**

Run: `venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"`
Expected: OK.

- [ ] **Step 7: Commit**

```bash
git add visualization/dash/portfolio_dashboard/tabs/chat_tab.py \
        visualization/dash/portfolio_dashboard/tabs/assets_tab.py \
        visualization/dash/portfolio_dashboard/tabs/hypotheticals_tab.py \
        tests/libraries/test_responsive_layout.py
git commit -m "feat: responsive chat height and non-overflow pass on remaining tabs

Chat has no grid layout, so it needed only a viewport-relative thread
height. Assets and Hypotheticals get responsive spans so they stop forcing
horizontal scroll; neither gets a card view, pending real phone use.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Browser acceptance check

The test that actually decides whether the goal is met. Everything before this asserts properties of a component tree; this measures the rendered page at phone width, which is what the 44px table and the 1401px scroll width were only ever visible in.

**Files:**
- Create: `tests/libraries/test_mobile_viewport.py`
- Modify: `README.md` (document how to run it)

**Interfaces:**
- Consumes: all previous tasks.
- Produces: `python tests/libraries/test_mobile_viewport.py` as a runnable acceptance check.

- [ ] **Step 1: Write the acceptance check**

This one is not a unittest module — it drives a live server. Create `tests/libraries/test_mobile_viewport.py`:

```python
#!/usr/bin/env python
"""
Mobile viewport acceptance check.

Renders the real app and measures element geometry at phone width. This is
deliberately separate from the unittest suite: it needs a live server, and it
is the only check that catches what the layout assertions cannot -- the
Portfolio chart collapsing to 233px and the milestones table to 44px while the
page scrolled 1401px wide.

Usage:
    PORTFOLIO_READ_ONLY=1 venv/bin/python tests/libraries/test_mobile_viewport.py

Exits non-zero on failure so it can gate a release.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PHONE_WIDTH = 390
MIN_USABLE_WIDTH = 200      # below this a chart or table is not readable
PORT = 8051                 # not 8050: do not collide with a running dev server

def _assert_served_layout_is_responsive(port):
    """
    Assert against the layout the server actually SENDS, not the Python
    objects. tests/libraries/test_responsive_layout.py checks the objects; this
    catches anything lost between construction and serialisation.

    Returns a list of failure strings (empty means pass).
    """
    with urllib.request.urlopen(
            f'http://localhost:{port}/_dash-layout', timeout=30) as resp:
        layout = json.loads(resp.read().decode())

    failures = []
    fixed_spans = []
    card_containers = []
    px_heights = []

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return

        props = node.get('props', {}) or {}
        node_type = node.get('type')

        if node_type == 'GridCol' and 'span' in props:
            if not isinstance(props['span'], dict):
                fixed_spans.append(props['span'])

        if props.get('hiddenFrom') == 'md' and props.get('id'):
            card_containers.append(props['id'])

        if node_type == 'Graph':
            height = str((props.get('style') or {}).get('height', ''))
            if 'px' in height:
                px_heights.append(props.get('id'))

        visit(props.get('children'))

    visit(layout)

    if fixed_spans:
        failures.append(
            f'{len(fixed_spans)} GridCol span(s) are fixed integers, not '
            f'responsive dicts: {sorted(set(map(str, fixed_spans)))}')
    if not card_containers:
        failures.append('no mobile card containers (hiddenFrom="md") served')
    if px_heights:
        failures.append(f'Graph(s) with fixed px height: {px_heights}')

    print(f'  responsive spans: {"ok" if not fixed_spans else "FAILED"}')
    print(f'  card containers served: {len(card_containers)}')
    return failures


def main():
    proc = subprocess.Popen(
        [sys.executable,
         'visualization/dash/portfolio_dashboard/portfolio_dashboard.py'],
        env={**os.environ, 'PORTFOLIO_READ_ONLY': '1', 'PORT': str(PORT)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://localhost:{PORT}', timeout=1)
                break
            except Exception:
                time.sleep(1)
        else:
            print('FAIL: server did not start')
            return 1

        print(f'Server up on {PORT}.')
        failures = _assert_served_layout_is_responsive(PORT)
        if failures:
            for failure in failures:
                print(f'FAIL: {failure}')
            return 1

        print()
        print('Served layout is responsive. Remaining checks need a rendered '
              'page (no browser driver is installed, and adding one would '
              'breach the no-new-dependencies constraint):')
        print(f'  - document.scrollWidth close to {PHONE_WIDTH} '
              f'(baseline before this work: 1401)')
        print(f'  - every visible chart/grid >= {MIN_USABLE_WIDTH}px '
              f'(baseline: chart 233px, table 44px)')
        print('Use the JS snippet in the plan, or devtools device emulation.')
        return 0
    finally:
        proc.terminate()


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: Run it to confirm the server starts**

Run: `PORTFOLIO_READ_ONLY=1 venv/bin/python tests/libraries/test_mobile_viewport.py`
Expected: exit 0, prints the assertions to check.

- [ ] **Step 3: Measure the real page at 390px**

The controller running this plan has browser automation available and should perform the measurement directly against a running server rather than relying on the script's printout. Start the app, then in the browser:

```javascript
const prev = document.body.style.cssText;
document.body.style.width = '390px';
window.dispatchEvent(new Event('resize'));
await new Promise(r => setTimeout(r, 2000));

const doc = document.documentElement;
const widths = {};
document.querySelectorAll('.js-plotly-plot, .ag-root-wrapper').forEach(el => {
  const host = el.closest('[id]');
  widths[host ? host.id : el.className] = Math.round(el.getBoundingClientRect().width);
});
const result = { scrollWidth: doc.scrollWidth, widths };
document.body.style.cssText = prev;
result;
```

Record the numbers. Acceptance:
- Portfolio chart width **> 200px** (was 233px at span=9 — must now be near full width)
- Any visible AgGrid width **> 200px**, or the grid is hidden and the card container is visible instead (was 44px)
- No element forces the page far past 390px

Compare against the pre-change baseline recorded in the spec: chart 233px, table 44px, page scrollWidth 1401px.

- [ ] **Step 4: Verify the desktop layout did not move**

Repeat the measurement at 1440px and confirm the chart and table widths match what they were before this branch. This is the non-regression check that justifies pinning every span at `md`.

- [ ] **Step 5: Document the check in `README.md`**

Add to the testing section:

```markdown
### Mobile viewport check

```bash
PORTFOLIO_READ_ONLY=1 python tests/libraries/test_mobile_viewport.py
```

Starts the app on port 8051 and prints the geometry assertions to verify at
390px. Separate from the unittest suite because it needs a live server. The
layout assertions in `tests/libraries/test_responsive_layout.py` run offline
as part of the normal suite.
```

- [ ] **Step 6: Full suite and commit**

```bash
venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"
git add tests/libraries/test_mobile_viewport.py README.md
git commit -m "test: add mobile viewport acceptance check

Layout assertions verify the component tree; this measures the rendered
page, which is the only place the 44px table and 1401px scroll width were
ever visible. Runs against a live server on port 8051 so it does not
collide with a dev server on 8050.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Checklist

After all tasks:

- [ ] `venv/bin/python -m unittest discover -s tests -t . -p "test_*.py"` passes
- [ ] At 390px: no chart or table under 200px wide; page does not scroll far past 390px
- [ ] At 1440px: chart and table widths identical to the pre-branch baseline
- [ ] Every tab's chart payload still under 500 KB (Assets is the binding case)
- [ ] `grep -rn "window_days\|DOWNSAMPLE_DAILY_WINDOW_DAYS\|ASSETS_DOWNSAMPLE_WINDOW_DAYS" --include=*.py . | grep -v venv` returns nothing
- [ ] Demo mode (`--demo`) still works
- [ ] Read-only mode still makes zero market-data network calls
