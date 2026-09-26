#!/usr/bin/env python3
"""Generate two synthetic PBIP projects: `healthy/` and `broken/`.

Same sales-pipeline report in both. `broken/` plants one defect per check, each one a
mistake that really happens in production reports:

  1. filters   a card filtered to Stage Order IN (2,3) with a measure that does MAX(Order)
  2. filters   a hand-edited filter with a repeated value: Order IN (3,3)
  3. family    a bar chart crossing "Deals Segment 1" with "Avg days Segment 2"
  4. refs      a visual pointing to a measure that was renamed in the model
  5. geometry  a leftover duplicate chart exactly on top of the original
  6. geometry  a visual that falls outside the canvas
  7. tmdl      a /// description followed by a blank line (Desktop won't open it)

All data lives in DATATABLE calculated tables: the model needs no data source or
gateway, so it can be published to any Pro workspace to try `pbip publish`.

    python3 examples/make_samples.py
"""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"

STAGES = """\
table Stages

	column StageId
		dataType: int64
		formatString: 0
		summarizeBy: none
		sourceColumn: [StageId]

	column Stage
		dataType: string
		summarizeBy: none
		sourceColumn: [Stage]
		sortByColumn: Order

	column Order
		dataType: int64
		formatString: 0
		summarizeBy: none
		sourceColumn: [Order]

	partition Stages = calculated
		mode: import
		source =
				DATATABLE(
				    "StageId", INTEGER, "Stage", STRING, "Order", INTEGER,
				    {
				        {1, "Lead", 1}, {2, "Qualified", 2}, {3, "Proposal", 3}, {4, "Won", 4}
				    }
				)
"""

DEALS_HEAD = """\
table Deals

	measure Deals = COUNTROWS(Deals)
		formatString: #,0
{description}	measure 'Deals at stage' =
			VAR _order = MAX(Stages[Order])
			RETURN CALCULATE(COUNTROWS(Deals), Stages[Order] = _order)
		formatString: #,0

	measure 'Deals Segment 1' =
			VAR _order = MAX(Stages[Order])
			RETURN CALCULATE(COUNTROWS(Deals), Stages[Order] = _order, Deals[Segment] = "SMB")
		formatString: #,0

	measure 'Deals Segment 2' =
			VAR _order = MAX(Stages[Order])
			RETURN CALCULATE(COUNTROWS(Deals), Stages[Order] = _order, Deals[Segment] = "Enterprise")
		formatString: #,0

	measure 'Avg days Segment 1' =
			VAR _order = MAX(Stages[Order])
			RETURN CALCULATE(AVERAGE(Deals[Days]), Stages[Order] = _order, Deals[Segment] = "SMB")
		formatString: #,0.0

	measure 'Avg days Segment 2' =
			VAR _order = MAX(Stages[Order])
			RETURN CALCULATE(AVERAGE(Deals[Days]), Stages[Order] = _order, Deals[Segment] = "Enterprise")
		formatString: #,0.0

	column DealId
		dataType: int64
		formatString: 0
		summarizeBy: none
		sourceColumn: [DealId]

	column StageId
		dataType: int64
		formatString: 0
		summarizeBy: none
		sourceColumn: [StageId]

	column Segment
		dataType: string
		summarizeBy: none
		sourceColumn: [Segment]

	column Days
		dataType: int64
		formatString: 0
		summarizeBy: sum
		sourceColumn: [Days]

	partition Deals = calculated
		mode: import
		source =
				DATATABLE(
				    "DealId", INTEGER, "StageId", INTEGER, "Segment", STRING, "Days", INTEGER,
				    {
				{rows}
				    }
				)
"""

GOOD_DESCRIPTION = """
	/// Deals at the stage of the current row. Resolves the row with MAX(Order):
	/// the visual must give it exactly one stage.
"""
# The blank line after the description orphans it -> InvalidLineType: Empty.
BROKEN_DESCRIPTION = """
	/// Deals at the stage of the current row. Resolves the row with MAX(Order).

"""


def deal_rows() -> str:
    """40 deterministic synthetic deals spread over the four stages and two segments."""
    rows = []
    for i in range(1, 41):
        stage = 1 + (i * 7) % 4
        segment = "SMB" if i % 3 else "Enterprise"
        rows.append(f'{{{i}, {stage}, "{segment}", {3 + (i * 11) % 40}}}')
    return ",\n".join("\t\t\t\t        " + r for r in rows)


def lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def measure(prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "Deals"}}, "Property": prop}}


def column(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def projection(field: dict) -> dict:
    kind = "Measure" if "Measure" in field else "Column"
    ref = field[kind]
    entity = ref["Expression"]["SourceRef"]["Entity"]
    return {"field": field, "queryRef": f"{entity}.{ref['Property']}",
            "nativeQueryRef": ref["Property"]}


def in_filter(name: str, values: list[str]) -> dict:
    """A categorical visual filter Stages[Order] IN (values)."""
    return {
        "name": name,
        "field": column("Stages", "Order"),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "s", "Entity": "Stages", "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}},
                                            "Property": "Order"}}],
                "Values": [[{"Literal": {"Value": v}}] for v in values]}}}],
        },
        "howCreated": "User",
    }


def visual(vid, vtype, x, y, w, h, roles, filters=None, z=0) -> dict:
    v = {
        "$schema": f"{SCHEMA}/item/report/definition/visualContainer/2.1.0/schema.json",
        "name": vid,
        "position": {"x": x, "y": y, "z": z, "height": h, "width": w},
        "visual": {"visualType": vtype, "query": {"queryState": {
            role: {"projections": [projection(f) for f in fields]}
            for role, fields in roles.items()}}},
    }
    if filters:
        v["filterConfig"] = {"filters": filters}
    return v


def visuals(broken: bool) -> list[dict]:
    """The page's visuals. With broken=True, the planted defects are added."""
    vs = [
        visual("a0000000000000000001", "cardVisual", 20, 20, 240, 110,
               {"Data": [measure("Deals")]}),
        # Deals at stage, correctly pinned to ONE stage.
        visual("a0000000000000000002", "cardVisual", 280, 20, 240, 110,
               {"Data": [measure("Deals at stage")]}, [in_filter("f2", ["3L"])]),
        # Same metric with two segment suffixes, grouped by stage: a deliberate comparison.
        visual("a0000000000000000003", "tableEx", 20, 150, 500, 250,
               {"Values": [column("Stages", "Stage"), measure("Deals Segment 1"),
                           measure("Deals Segment 2")]}),
        visual("a0000000000000000004", "clusteredBarChart", 540, 150, 700, 250,
               {"Category": [column("Stages", "Stage")], "Y": [measure("Deals")]}),
    ]
    if not broken:
        return vs
    vs += [
        # 1. Multi-value filter + MAX(Order) -> the card computes a different stage.
        visual("b0000000000000000001", "cardVisual", 540, 20, 240, 110,
               {"Data": [measure("Deals at stage")]}, [in_filter("f5", ["2L", "3L"])]),
        # 2. Hand-edited filter with a repeated value.
        visual("b0000000000000000002", "cardVisual", 800, 20, 240, 110,
               {"Data": [measure("Deals")]}, [in_filter("f6", ["3L", "3L"])]),
        # 3. Volume of one segment with the time of another, in the same chart.
        visual("b0000000000000000003", "clusteredBarChart", 20, 420, 600, 280,
               {"Category": [column("Stages", "Stage")], "Y": [measure("Deals Segment 1")],
                "Tooltips": [measure("Avg days Segment 2")]}),
        # 4. Points to a measure renamed in the model long ago.
        visual("b0000000000000000004", "cardVisual", 1060, 20, 200, 110,
               {"Data": [measure("Total deals")]}),
        # 5. Leftover duplicate right on top of the bar chart a...04.
        visual("b0000000000000000005", "clusteredBarChart", 545, 152, 700, 250,
               {"Category": [column("Stages", "Stage")], "Y": [measure("Deals")]}, z=5),
        # 6. Falls outside the 1280x720 canvas.
        visual("b0000000000000000006", "cardVisual", 1150, 640, 240, 110,
               {"Data": [measure("Deals")]}),
    ]
    return vs


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build(root: Path, broken: bool) -> None:
    if root.exists():
        shutil.rmtree(root)
    name = "Sales"
    rep, mod = root / f"{name}.Report", root / f"{name}.SemanticModel"

    write_json(root / f"{name}.pbip", {
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
        "artifacts": [{"report": {"path": f"{name}.Report"}}],
        "settings": {"enableAutoRecovery": True}})

    # ---- semantic model
    for kind, folder, lid in (("SemanticModel", mod, "7f1c2b9e-0d4a-4a55-9a51-5e3c1d2b0a01"),
                              ("Report", rep, "7f1c2b9e-0d4a-4a55-9a51-5e3c1d2b0a02")):
        write_json(folder / ".platform", {
            "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": kind, "displayName": name},
            "config": {"version": "2.0", "logicalId": lid}})
    write_json(mod / "definition.pbism", {
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    d = mod / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n\n")
    (d / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n\nref table Stages\nref table Deals\n\n")
    (d / "relationships.tmdl").write_text(
        "relationship deals_stage\n\tfromColumn: Deals.StageId\n\ttoColumn: Stages.StageId\n\n")
    (d / "tables" / "Stages.tmdl").write_text(STAGES)
    (d / "tables" / "Deals.tmdl").write_text(DEALS_HEAD
        .replace("{description}", BROKEN_DESCRIPTION if broken else GOOD_DESCRIPTION)
        .replace("{rows}", deal_rows()))

    # ---- report
    write_json(rep / "definition.pbir", {
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    rd = rep / "definition"
    write_json(rd / "version.json", {
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0"})
    write_json(rd / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/2.1.0/schema.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61",
                                          "type": "SharedResources"}},
        "settings": {"useStylableVisualContainerHeader": True,
                     "defaultDrillFilterOtherVisuals": True}})
    page_id = "c0000000000000000001"
    write_json(rd / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [page_id], "activePageName": page_id})
    write_json(rd / "pages" / page_id / "page.json", {
        "$schema": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json",
        "name": page_id, "displayName": "Pipeline", "displayOption": "FitToPage",
        "height": 720, "width": 1280})
    for v in visuals(broken):
        write_json(rd / "pages" / page_id / "visuals" / v["name"] / "visual.json", v)


if __name__ == "__main__":
    build(HERE / "healthy", broken=False)
    build(HERE / "broken", broken=True)
    print("generated examples/healthy and examples/broken")
