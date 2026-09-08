"""Generate an editable draw.io file of the four-lane sequence diagrams.

draw.io opens plain mxGraph XML, so this emits real shapes -- boxes, dashed lifelines,
labelled arrows, note cards -- that can be dragged and retyped in the browser. It is not a
flattened image and not an embedded SVG.

    python scripts/make_drawio.py            # writes docs/diagrams/sequence-diagrams.drawio

The diagrams are declared as data below, so a change to the flow is an edit here rather
than hand-editing XML. The same content drives the Mermaid sources in docs/diagrams/.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "sequence-diagrams.drawio"

LANE_W, LANE_H, LANE_GAP = 190, 50, 300
TOP, ROW, NOTE_ROW, SECTION_ROW = 40, 66, 92, 58
LEFT = 40

BLUE = "fillColor=#dae8fc;strokeColor=#6c8ebf;"
NOTE = "fillColor=#fff2cc;strokeColor=#d6b656;"
SECTION = "fillColor=#e1e9f7;strokeColor=#9db2dc;"
WRITE1 = "fillColor=#fff4e0;strokeColor=#d6b656;"
WRITE2 = "fillColor=#e5f6ec;strokeColor=#82b366;"

# ("lane", label) declares a participant.
# Steps: ("msg", src, dst, label) solid; ("ret", src, dst, label) dashed;
#        ("note", lane, text); ("section", text); ("band", text, style)
DIAGRAMS: list[tuple[str, list[tuple[str, str]], list[tuple]]] = [
    (
        "Shopping — chairs under $1000",
        [
            ("B", "Browser"),
            ("A", "Agent\n(our process)"),
            ("M", "Anthropic\nMessages API"),
            ("CT", "commercetools"),
        ],
        [
            ("note", "B", 'User types\n"show me some chairs under $1000"'),
            ("msg", "B", "A", "POST /api/chat"),
            ("msg", "A", "CT", "read the session's cart\n(grounding, before any model call)"),
            ("ret", "CT", "A", "Cart — empty"),
            ("section", "ROUND 1"),
            ("msg", "A", "M", "static prompt + 21 tool definitions + the message"),
            ("ret", "M", "A", '"call search_products"\n{query "chair", filters {max_price 1000}}'),
            (
                "note",
                "A",
                "Inside our process, in this order\n"
                "1. tool name to Python function — one dict lookup\n"
                "2. gates run FIRST — provenance, quantity caps\n"
                "3. then the backend method is allowed to call out",
            ),
            ("msg", "A", "CT", "GraphQL productProjectionSearch\n+ price selection, + locale"),
            ("ret", "CT", "A", "5 raw product projections (34 KB)"),
            (
                "note",
                "A",
                "Mapped to typed records\n"
                "options from attributes that DIFFER across variants\n"
                "price.discounted.value, not the list price\n"
                "name from nameAllLocales\n"
                "then fenced as untrusted data, capped 12k chars",
            ),
            ("section", "ROUND 2 — the tool result goes back as a message"),
            ("msg", "A", "M", "same conversation + the fenced product list"),
            ("ret", "M", "A", '"call present_products"\n{5 picks, each with a reason}'),
            (
                "note",
                "A",
                "Every id checked against what this session saw.\n"
                "Card filled in from that record. NO commercetools call.",
            ),
            ("ret", "A", "B", "SSE ui event — product cards render"),
            ("section", "ROUND 3"),
            ("msg", "A", "M", "same conversation + result"),
            ("ret", "M", "A", '"call present_suggestions", then end_turn'),
            (
                "ret",
                "A",
                "B",
                "SSE ui + text, then turn_complete\n"
                "cache_read 31181 — the prompt prefix was reused",
            ),
            (
                "note",
                "B",
                "3 model calls. 2 commercetools calls. 10.5s.\n"
                "Hosts: api.anthropic.com, api.commercetools.com, auth.commercetools.com\n"
                "No MCP endpoint, in either direction.",
            ),
        ],
    ),
    (
        "Merchant read — slow sellers",
        [
            ("B", "Browser\n(back office)"),
            ("A", "Agent\n(our process)"),
            ("M", "Anthropic\nMessages API"),
            ("CT", "commercetools"),
        ],
        [
            (
                "note",
                "B",
                'Operator types\n"give me a list of slow selling\nproducts from the past week"',
            ),
            ("msg", "B", "A", "POST /api/merchant/chat"),
            (
                "note",
                "A",
                "The operator identity comes from server-side config.\n"
                "No request body ever names an operator or a merchant.",
            ),
            ("section", "ROUND 1"),
            ("msg", "A", "M", "static prompt + 21 tool definitions + the message"),
            ("ret", "M", "A", '"call load_skill" {inventory-operations}'),
            (
                "note",
                "A",
                "Read from the in-process skill registry.\n"
                "Skills are Anthropic's flow files, unchanged. No platform call.",
            ),
            ("section", "ROUND 2"),
            ("msg", "A", "M", "same conversation + the skill text"),
            ("ret", "M", "A", '"call get_inventory_alerts" {}'),
            (
                "note",
                "A",
                "commercetools has no alerts object.\nOne tool call becomes four kinds of read.",
            ),
            ("msg", "A", "CT", "1. inventory where availableQuantity under 10"),
            ("ret", "CT", "A", "candidate SKUs"),
            ("msg", "A", "CT", "2. inventory for those SKUs, all supply channels"),
            ("ret", "CT", "A", "per-channel quantities"),
            ("msg", "A", "CT", "3. orders from the last 30 days"),
            ("ret", "CT", "A", "order lines, for sales velocity"),
            ("msg", "A", "CT", "4. one product lookup per flagged SKU (4 in parallel)"),
            ("ret", "CT", "A", "listings"),
            (
                "note",
                "A",
                "Alert derived, not read\n"
                "sellable = the channel-less entry, what the shop can actually sell\n"
                "other channels reported separately, so nobody reorders stock they own\n"
                'the result carries a note "the window is 30 days"',
            ),
            ("section", "ROUND 3"),
            ("msg", "A", "M", "same conversation + the fenced alerts"),
            ("ret", "M", "A", '"call present_digest" {items}'),
            ("ret", "A", "B", "SSE ui event — digest card renders"),
            ("section", "ROUND 4"),
            ("msg", "A", "M", "same conversation + result"),
            ("ret", "M", "A", '"call present_suggestions", then end_turn'),
            ("ret", "A", "B", "SSE ui + text, then turn_complete"),
            (
                "note",
                "B",
                "The reply corrects the question\n"
                '"the data covers 30 days, not a week —\nno weekly pace is tracked"',
            ),
            ("note", "B", "4 model calls. 8 commercetools calls. 11.5s.\nNo MCP endpoint."),
        ],
    ),
    (
        "Merchant write — the approval gate",
        [
            ("B", "Browser\n(back office)"),
            ("A", "Agent\n(our process)"),
            ("M", "Anthropic\nMessages API"),
            ("CT", "commercetools"),
        ],
        [
            ("note", "B", 'Operator types\n"restock the Classic Serving Tray"'),
            ("band", "REQUEST 1 — the agent proposes. Nothing is written.", WRITE1),
            ("msg", "B", "A", "POST /api/merchant/chat"),
            ("msg", "A", "M", "static prompt + tools + the message"),
            ("ret", "M", "A", '"call stage_inventory_action" {items}'),
            ("msg", "A", "CT", "read current stock and other-channel units"),
            ("ret", "CT", "A", "quantities"),
            (
                "note",
                "A",
                "Guardrails checked at staging\n"
                "500 units per restock, 25 items per change, 20% per price move\n"
                "then the proposal is persisted, status = staged",
            ),
            (
                "ret",
                "A",
                "B",
                "SSE ui — change preview card\n"
                'Approve / Dismiss / "Nothing applies until you approve"',
            ),
            ("band", "REQUEST 2 — a human clicks Approve. Only now is anything written.", WRITE2),
            ("msg", "B", "A", "POST /api/merchant/changes/{id}/apply"),
            (
                "note",
                "A",
                "The approval mark is set for THIS call only\n"
                "gate — the id must be one this session saw\n"
                "AND one a human approved\n"
                "staged status and guardrails re-checked BEFORE the write",
            ),
            ("msg", "A", "CT", "POST /inventory/{id} addQuantity"),
            ("ret", "CT", "A", "200"),
            (
                "note",
                "A",
                "Only now marked applied, stamped with who approved it.\n"
                "The approval mark is cleared, whatever the outcome.",
            ),
            ("ret", "A", "B", "{ok true} — the shop's next read sees the new stock"),
            (
                "note",
                "B",
                "The model can propose. It cannot approve.\n"
                "Nothing typed in the chat can approve anything.",
            ),
        ],
    ),
]


def build() -> str:
    mxfile = ET.Element("mxfile", host="app.diagrams.net", type="device")
    for index, (title, lanes, steps) in enumerate(DIAGRAMS, start=1):
        diagram = ET.SubElement(mxfile, "diagram", id=f"d{index}", name=title)
        model = ET.SubElement(
            diagram, "mxGraphModel", grid="1", gridSize="10", page="1", math="0", shadow="0"
        )
        root = ET.SubElement(model, "root")
        ET.SubElement(root, "mxCell", id="0")
        ET.SubElement(root, "mxCell", id="1", parent="0")

        centre = {key: LEFT + i * LANE_GAP + LANE_W / 2 for i, (key, _) in enumerate(lanes)}
        width = LEFT * 2 + (len(lanes) - 1) * LANE_GAP + LANE_W
        uid = [0]

        def cell(
            _root: ET.Element = root, _i: int = index, _u: list[int] = uid, **kw: str
        ) -> ET.Element:
            _u[0] += 1
            return ET.SubElement(_root, "mxCell", id=f"c{_i}-{_u[0]}", parent="1", **kw)

        def box(x: float, y: float, w: float, h: float, text: str, style: str) -> None:
            c = cell(value=text, style=f"rounded=0;whiteSpace=wrap;html=1;{style}", vertex="1")
            ET.SubElement(
                c,
                "mxGeometry",
                x=str(x),
                y=str(y),
                width=str(w),
                height=str(h),
                attrib_as="geometry",
            ).set("as", "geometry")

        # Bands and sections are drawn first so arrows sit on top of them.
        y = TOP + LANE_H + 30
        rows: list[tuple] = []
        for step in steps:
            kind = step[0]
            rows.append((y, step))
            y += (
                SECTION_ROW
                if kind in ("section", "band")
                else (NOTE_ROW + 14 * step[2].count("\n") if kind == "note" else ROW)
            )
        bottom = y + 30

        for row_y, step in rows:
            if step[0] == "section":
                box(LEFT + 20, row_y - 6, width - LEFT * 2 - 40, 34, step[1], SECTION)
            elif step[0] == "band":
                box(LEFT + 10, row_y - 6, width - LEFT * 2 - 20, 34, step[1], step[2])

        for key, label in lanes:
            x = centre[key] - LANE_W / 2
            box(x, TOP, LANE_W, LANE_H, label, BLUE)
            box(x, bottom, LANE_W, LANE_H, label, BLUE)
            line = cell(
                style="endArrow=none;dashed=1;html=1;strokeColor=#9673a6;strokeWidth=2;", edge="1"
            )
            geo = ET.SubElement(line, "mxGeometry", relative="1")
            geo.set("as", "geometry")
            ET.SubElement(geo, "mxPoint", x=str(centre[key]), y=str(TOP + LANE_H)).set(
                "as", "sourcePoint"
            )
            ET.SubElement(geo, "mxPoint", x=str(centre[key]), y=str(bottom)).set(
                "as", "targetPoint"
            )

        for row_y, step in rows:
            kind = step[0]
            if kind in ("msg", "ret"):
                _, src, dst, label = step
                dashed = "dashed=1;" if kind == "ret" else ""
                edge = cell(
                    value=label,
                    style=f"endArrow=block;html=1;{dashed}strokeColor=#333333;"
                    "verticalAlign=bottom;labelBackgroundColor=#ffffff;",
                    edge="1",
                )
                geo = ET.SubElement(edge, "mxGeometry", relative="1")
                geo.set("as", "geometry")
                ET.SubElement(geo, "mxPoint", x=str(centre[src]), y=str(row_y)).set(
                    "as", "sourcePoint"
                )
                ET.SubElement(geo, "mxPoint", x=str(centre[dst]), y=str(row_y)).set(
                    "as", "targetPoint"
                )
            elif kind == "note":
                _, lane, text = step
                lines = text.count("\n") + 1
                w = 460
                h = 22 + 16 * lines
                x = min(max(centre[lane] - w / 2, 10), width - w - 10)
                box(x, row_y - 10, w, h, text.replace("\n", "<br/>"), NOTE)

        model.set("dx", str(width + 100))
        model.set("dy", str(bottom + 200))
    return ET.tostring(mxfile, encoding="unicode")


if __name__ == "__main__":
    xml = build()
    OUT.write_text(xml, encoding="utf-8")
    parsed = ET.fromstring(xml)
    diagrams = parsed.findall("diagram")
    print(f"wrote {OUT.relative_to(Path.cwd())}  ({len(xml):,} bytes)")
    for d in diagrams:
        cells = d.findall(".//mxCell")
        print(f"  {d.get('name'):<40} {len(cells)} cells")
