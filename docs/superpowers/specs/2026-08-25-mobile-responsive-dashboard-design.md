# Mobile-responsive dashboard

**Date:** 2026-08-25
**Status:** Approved (design)

## Problem

The dashboard is unusable on a phone. Measured against the running app with the
body constrained to iPhone 14 width (390px):

| Element | Width at 390px | Cause |
|---|---|---|
| Portfolio chart | **233px** | `span=9` is a fixed 9/12 = 75% |
| Milestones table | **44px** | `span=3` is a fixed 3/12 = 25% |
| Page scroll width | **1401px** | nothing collapses |

Mantine's grid spans are fixed fractions, not responsive: `span=3` means 25% of
the container at every screen size. On a phone that leaves the milestones table
44 pixels wide while the page scrolls sideways for over a kilometre. Chart
heights are hardcoded too — `height=800` on the dimension tabs, `450` on
Portfolio — so a single chart is taller than the screen it is being viewed on.

Two plausible causes were checked and ruled out. Dash 4 **already** emits
`<meta name="viewport" content="width=device-width, initial-scale=1">`, and the
tab bar **already** scrolls horizontally rather than overflowing. Neither is the
problem; the problem is entirely fixed-fraction layout and fixed pixel heights.

### Why now

Goal 3 is hosting this so it can be reached from a phone. Payloads are already
under 500 KB per tab after the read-only web tier work
(`2026-08-24-read-only-web-tier-design.md`), so bytes are no longer the
obstacle — layout is.

## Decision

Make six of the eight tabs genuinely usable at phone width using
dash-mantine-components' native responsive primitives, with **no change to the
desktop layout at any breakpoint from `md` (992px) upward**.

dmc 2.8.0 supports everything required natively — verified against the
installed version:

- responsive span dicts: `span={"base": 12, "md": 3}`
- `visibleFrom` / `hiddenFrom` breakpoint visibility
- `Card`, `SimpleGrid`, `Drawer`, `Burger`

So the work is declarative. No custom CSS media queries, no JavaScript, and no
server-side viewport detection.

### Scope

**In:** `dimension_tab_factory` (which covers Sectors, Asset Types, Account
Types and Geography — four tabs from one file), `portfolio_tab`, `chat_tab`, the
shared responsive-table component, and the point-budget downsampler.

**Minimal pass only:** `assets_tab` and `hypotheticals_tab` get responsive spans
so they stop overflowing, but no card view. Both are desktop-shaped by nature —
Assets is a dense column-comparison table, Hypotheticals is a dropdown-driven
exploratory tool with ~102 series. Whether they deserve full mobile treatment is
better answered after using the phone view for a while than guessed at now.

**Out:** authentication, TLS, hosting (goal 3); navigation redesign (the
existing scrolling tab bar is kept); server-side viewport detection.

### Rejected alternatives

**Viewport width reported to the server via `dcc.Store`.** Would let chart
callbacks send fewer series on mobile. Rejected: payloads are already under
500 KB, so this is a legibility problem rather than a bandwidth one, and it
would add a `State` to every chart callback plus a first-render race for no
measured benefit.

**Serving the reduced series set everywhere.** Simplest code, but it degrades
the desktop experience to serve the phone.

**Burger menu / bottom tab bar.** The tab bar already scrolls horizontally, so
the added Drawer state and second navigation concept buys little. Keeping one
navigation model means tab position stays familiar across devices.

## Architecture

### 1. Breakpoint convention

`md` (992px) is the divide. Every responsive span keeps its current value at
`md` and above, and goes full width below:

```python
span={"base": 12, "md": 3}     # was span=3
span={"base": 12, "md": 9}     # was span=9
```

Offsets (`offset=1`, `offset=8`) drop to `0` at `base` — an offset on a
full-width column just wastes screen.

This convention is what guarantees the desktop layout is unchanged: at `md`+
every value is exactly what it is today.

### 2. `components/responsive_table.py` (new)

Renders both views; CSS picks one.

```python
def responsive_table(table_id, column_defs, row_data, primary_field,
                     mobile_fields, **grid_kwargs):
    """Desktop AgGrid + mobile card stack. Only one is ever visible."""
```

- `dmc.Box(dag.AgGrid(id=table_id, ...), visibleFrom="md")` — today's grid
- `dmc.Box(id=f"{table_id}-cards", hiddenFrom="md")` — stacked `dmc.Card`s

Card content is produced by a pure function:

```python
def build_mobile_cards(row_data, primary_field, mobile_fields) -> list:
    """One dmc.Card per row: primary_field as the heading, mobile_fields as
    label/value pairs beneath. Pure — no Dash context, no I/O."""
```

Callbacks that already compute `row_data` gain one `Output` targeting
`{table_id}-cards` and call `build_mobile_cards` on the same data. No new state,
no clientside code.

**Accepted cost:** row data is serialised twice per response. For the largest
table in scope (34 rows) this is negligible beside a ~220 KB chart payload, and
it buys a design with no JavaScript and no synchronisation between the two
views.

`mobile_fields` should be 3–4 fields; more than that reproduces the unreadable
wide table in card form.

### 3. Charts

Fixed heights are removed from the figures. Instead:

```python
dcc.Graph(id=..., style={"height": "60vh"}, config={"responsive": True})
fig.update_layout(autosize=True)          # replaces height=800 / height=450
```

A chart then occupies a sensible fraction of whatever screen it is on rather
than a fixed pixel count taller than a phone.

The legend moves below the plot, horizontal, so it wraps instead of consuming
horizontal space:

```python
fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.15))
```

Series filtering stays exactly where it is — the selectable table above each
chart. No new filtering UI.

### 4. Point-budget downsampling

`libraries/downsample.py` currently keeps daily resolution inside a fixed
calendar window (365 days generally, 60 for Assets) and thins to weekly beyond
it. Two problems: the payload grows ~70 KB/year with history regardless of the
window (recorded as a known follow-up in the read-only web tier work), and a
calendar window is unrelated to how many points a chart can actually render.

Replace it with a **point budget**: target roughly 300 points per series, with
an adaptive stride computed from the series length. Rationale for 300 — Sectors
today renders 8,425 points across 28 series, about 300 per series, and looks
correct; matching that number preserves the current appearance while making it
independent of how much history accumulates.

Endpoint preservation is retained exactly as it is today: each group's first and
last points always survive, so chart endpoints continue to agree with the
figures in the table beside them. The existing regression test covering the
non-unique-index case must keep passing.

`ASSETS_DOWNSAMPLE_WINDOW_DAYS` and `DOWNSAMPLE_DAILY_WINDOW_DAYS` are removed
once nothing references them.

**300 is chosen against the payload ceiling, not by feel.** The Assets tab is
the binding constraint — it has the most traces (34) and today sits at 455 KB,
only 9% under the 500 KB target:

| Budget/series | Assets points | Assets payload |
|---|---|---|
| 250 | 8,500 | ~359 KB |
| **300** | **10,200** | **~430 KB** |
| 350 | 11,900 | ~502 KB — over |

At 300 the Assets tab lands at roughly 430 KB, *better* than the 455 KB the
60-day calendar window produces today, and Sectors comes out at ~219 KB against
today's 220 KB — visually indistinguishable. At 350 Assets breaches the target,
so 300 sits close to a real ceiling; raising it later requires re-measuring
Assets rather than assuming headroom exists.

The important property is that these numbers no longer drift. Under the calendar
window the Assets payload grew about 70 KB per year of history and would have
crossed 500 KB within roughly a year; a point budget is invariant to how much
history accumulates.

### 5. Per-file changes

| File | Change | Tabs affected |
|---|---|---|
| `components/responsive_table.py` | new — grid + card views, pure card builder | shared |
| `libraries/downsample.py` | point budget replaces calendar window | all |
| `tabs/dimension_tab_factory.py` | responsive spans, responsive chart, responsive table | **4** |
| `tabs/portfolio_tab.py` | responsive spans, 3 tables, 2 charts | 1 |
| `tabs/chat_tab.py` | viewport-relative height only (no grid layout exists) | 1 |
| `tabs/assets_tab.py` | responsive spans only — stop overflowing | (minimal) |
| `tabs/hypotheticals_tab.py` | responsive spans only — stop overflowing | (minimal) |

## Error handling

This change is presentational; it introduces no new I/O and no new failure
modes. Two degradation cases are worth stating:

- **Empty `row_data`.** `build_mobile_cards([])` returns an empty list, so the
  mobile view renders nothing rather than raising. The desktop grid already
  handles empty data.
- **A `mobile_fields` entry missing from a row.** The field is skipped rather
  than rendering `None`. Callers control both sides, so a missing field is a
  programming error, but it must not blank the whole card.

## Testing

- **`build_mobile_cards` (pure).** One card per row; the primary field is the
  heading; only `mobile_fields` appear; empty input yields an empty list; a
  missing field is skipped rather than raising.
- **Point-budget downsampler.** Each group's first and last points survive; no
  series is dropped; output stays within the budget for a series far longer than
  the budget; a series shorter than the budget passes through unchanged; the
  existing non-unique-index regression test still passes.
- **Layout assertions.** Every `GridCol` in the touched tabs carries a
  responsive span dict rather than a bare integer; the responsive table emits
  both a `visibleFrom="md"` grid and a `hiddenFrom="md"` card container; no
  `dcc.Graph` in a touched tab carries a fixed pixel height.
- **Real browser check at 390px.** Load the app, constrain to 390px, and assert
  zero horizontal overflow and that no chart or table collapses below a usable
  width. This is the test that would have caught the 44px table and the 1401px
  scroll width, and it is the one that decides whether the goal is met.
- **Desktop non-regression.** At 1440px, chart and table widths must match the
  pre-change values. The whole point of pinning every span at `md` is that the
  laptop view does not move.

## Out of scope

- Authentication, TLS, secrets, hosting — goal 3 spec.
- Full mobile treatment of `assets_tab` and `hypotheticals_tab` — revisit after
  real phone use.
- Navigation redesign — the scrolling tab bar is kept deliberately.
- Server-side viewport detection.
- Native app or PWA packaging.
