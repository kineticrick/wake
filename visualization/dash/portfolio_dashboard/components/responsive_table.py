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
