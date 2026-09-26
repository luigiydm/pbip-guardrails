"""Visual <-> measure coherence checks for a PBIP project.

The question is not "is the report valid?" but "is it safe to use this measure
under the context this visual gives it?". The same visual.json can be right or
wrong depending on how the measure it consumes is written, and the two halves of
that contract live in different folders (X.Report and X.SemanticModel). No other
tool validates them together.

Checks:
    filters   multi-value visual filter on a column a measure resolves with MAX(...)
    family    one visual crossing the figure of one segment with another's
    refs      measures/columns the report references that no longer exist
    geometry  visuals outside the canvas, or one hiding a same-size visual
    tmdl      orphan /// description (blank line) that stops Desktop opening the project
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ALL_CHECKS = ("filters", "family", "refs", "geometry", "tmdl")

# Visuals that are usually background or decoration: they don't count for overlap.
DECORATIVE = {"shape", "image", "textbox", "actionButton", "basicShape"}

ROW_RESOLVERS = r"\b(?:MAX|MIN|SELECTEDVALUE|VALUES|FIRSTNONBLANK|LASTNONBLANK)\s*\("


@dataclass
class Finding:
    """One finding. `page` is None for model-level findings (tmdl)."""

    check: str
    page: str | None
    message: str
    why: str = ""

    def render(self) -> str:
        """Human-readable form, one or two lines."""
        text = f"  [{self.check}] {self.message}"
        if self.why:
            text += f"\n      -> {self.why}"
        return text


def read_json(path: Path):
    """Read a PBIR JSON tolerating the BOM that Power BI Desktop writes."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def walk(node, parents=()):
    """Yield (node, key path) for every node of a nested JSON."""
    yield node, parents
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, parents + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, parents + (str(i),))


def bookmark_targets(report_dir: Path) -> set:
    """Ids of visuals that some bookmark toggles.

    Two visuals in the same place and of the same size are NOT a duplicate if a
    bookmark alternates them: that is a deliberate interaction. Deleting one to get
    the checker green breaks the report.
    """
    ids = set()
    for bj in sorted(report_dir.glob("definition/bookmarks/*.json")):
        try:
            ids |= set(re.findall(r'"([0-9a-f]{16,32})"', bj.read_text(encoding="utf-8-sig")))
        except OSError:
            continue
    return ids


def visual_refs(visual) -> tuple[set, set]:
    """Return the sets of measures and columns a visual.json references."""
    measures, columns = set(), set()
    for node, _ in walk(visual):
        if not isinstance(node, dict):
            continue
        for key, target in (("Measure", measures), ("Column", columns)):
            ref = node.get(key)
            if isinstance(ref, dict) and isinstance(ref.get("Property"), str):
                target.add(ref["Property"])
    return measures, columns


def entity_of(ref, from_map) -> str | None:
    """Resolve the table of a column reference, through the From alias if needed."""
    if not isinstance(ref, dict):
        return None
    src = ((ref.get("Expression") or {}).get("SourceRef")) or {}
    if src.get("Entity"):
        return src["Entity"]
    return from_map.get(src.get("Source"))


def projected_columns(visual) -> set:
    """Return {(table, column)} the visual puts on rows / axes / values."""
    projected = set()
    qs = (((visual.get("visual") or {}).get("query")) or {}).get("queryState") or {}
    for container in qs.values():
        for proj in (container or {}).get("projections") or []:
            ref = (proj.get("field") or {}).get("Column")
            if isinstance(ref, dict) and ref.get("Property"):
                projected.add((entity_of(ref, {}), ref["Property"]))
    return projected


def multi_value_filters(visual) -> list:
    """Find `In` conditions with more than one value: the measure sees one context.

    If the measure resolves the row with MAX(col), a multi-value filter makes it
    compute a DIFFERENT step from the one the row shows (e.g. a conversion rate
    above 100%, because numerator and denominator come from different steps).

    Legit case discounted by the caller: if the visual groups by a column of the
    SAME table (filters Order IN (1,10,12) and puts Stage on the rows), the row
    context pins one value and MAX resolves correctly.
    """
    found = []
    projected = projected_columns(visual)
    for flt in (visual.get("filterConfig") or {}).get("filters") or []:
        body = (flt or {}).get("filter") or {}
        from_map = {f.get("Name"): f.get("Entity")
                    for f in (body.get("From") or []) if isinstance(f, dict)}
        for node, _ in walk(body):
            if not isinstance(node, dict):
                continue
            cond = node.get("In")
            if not isinstance(cond, dict):
                continue
            col, table = "?", None
            for e in cond.get("Expressions") or []:
                ref = (e or {}).get("Column") or {}
                if ref.get("Property"):
                    col = ref["Property"]
                    table = entity_of(ref, from_map)
            values = []
            for group in cond.get("Values") or []:
                for v in group or []:
                    lit = ((v or {}).get("Literal") or {}).get("Value")
                    if lit is not None:
                        values.append(str(lit))
            if len(values) <= 1:
                continue
            repeated = len(values) != len(set(values))
            grouped = any(t == table for t, _ in projected) if table else False
            found.append((col, values, repeated, grouped))
    return found


def _tmdl_files(model_dir: Path):
    return sorted(model_dir.glob("definition/tables/*.tmdl"))


def row_sensitive_measures(model_dir: Path) -> dict:
    """Return {measure: {(table, column) it resolves with MAX/SELECTEDVALUE/VALUES...}}.

    This is what turns a suspicion into evidence: a measure doing MAX(Stages[Order])
    DEPENDS on the row context. A measure that resolves nothing like that is immune
    and there is nothing to report.
    """
    sensitive = {}
    cut = re.compile(r"^\s*(measure|column|partition|hierarchy|calculationGroup)\s", re.M)
    for tmdl in _tmdl_files(model_dir):
        text = tmdl.read_text(encoding="utf-8-sig", errors="replace")
        marks = [m.start() for m in cut.finditer(text)] + [len(text)]
        for start, end in zip(marks, marks[1:]):
            block = text[start:end]
            head = re.match(r"^\s*measure\s+(?:'([^']+)'|([^\s'=]+))", block)
            if not head:
                continue
            name = head.group(1) or head.group(2)
            cols = set()
            for m in re.finditer(ROW_RESOLVERS + r"\s*(?:'([^']+)'|(\w+))\[([^\]]+)\]",
                                 block, re.I):
                cols.add((m.group(1) or m.group(2), m.group(3)))
            if cols:
                sensitive[name] = cols
    return sensitive


def model_objects(model_dir: Path) -> tuple[set, set, dict]:
    """Extract measure names, column names and column dataTypes from the model TMDL."""
    measures, columns, types = set(), set(), {}
    for tmdl in _tmdl_files(model_dir):
        text = tmdl.read_text(encoding="utf-8-sig", errors="replace")
        measures |= set(re.findall(r"^\s*measure\s+'([^']+)'", text, re.M))
        measures |= set(re.findall(r"^\s*measure\s+([^\s'=]+)\s*=", text, re.M))
        for m in re.finditer(
                r"^\s*column\s+(?:'([^']+)'|([^\s'=]+))(.*?)"
                r"(?=^\s*(?:column|measure|partition|hierarchy)\s|\Z)",
                text, re.M | re.S):
            name = m.group(1) or m.group(2)
            columns.add(name)
            dtype = re.search(r"dataType:\s*(\w+)", m.group(3) or "")
            if dtype:
                types[name] = dtype.group(1)
    return measures, columns, types


def orphan_descriptions(model_dir: Path) -> list:
    """Find the TMDL mistake that stops the WHOLE project from opening.

    A /// description belongs to the object right after it: a blank line in the
    middle leaves it orphaned -> `InvalidLineType: Empty` in Desktop.
    A lone "/// " line is a VALID paragraph separator (the official parser accepts
    it), so it is not reported.
    """
    found = []
    for tmdl in sorted(model_dir.rglob("*.tmdl")):
        lines = tmdl.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not line.strip().startswith("///"):
                continue
            nxt = next((lines[j] for j in range(i + 1, len(lines)) if lines[j].strip()), None)
            gap = i + 1 < len(lines) and not lines[i + 1].strip()
            if gap and not (nxt or "").strip().startswith("///"):
                found.append((tmdl, i + 1))
    return found


def run_checks(report_dir: Path, model_dir: Path | None = None, family: str | None = None,
               only: tuple[str, ...] = ALL_CHECKS) -> list[Finding]:
    """Run the requested checks and return the findings (empty list = clean).

    Always pass `model_dir` when you have it: without the model the `filters` check
    can't tell which measure depends on the filtered column and reports too much,
    and `refs` / `tmdl` don't run at all.
    """
    active = set(only)
    pages = sorted(report_dir.glob("definition/pages/*/page.json"))
    if not pages:
        raise FileNotFoundError(f"no pages found under {report_dir}/definition/pages/")

    findings: list[Finding] = []
    bookmarks = bookmark_targets(report_dir)
    m_measures, m_columns, types, sensitive = set(), set(), {}, {}
    if model_dir:
        m_measures, m_columns, types = model_objects(model_dir)
        sensitive = row_sensitive_measures(model_dir)

    for page_json in pages:
        pdata = read_json(page_json) or {}
        page = pdata.get("displayName") or page_json.parent.name
        width = pdata.get("width") or 1280
        height = pdata.get("height") or 720
        geo = []

        for vjson in sorted(page_json.parent.glob("visuals/*/visual.json")):
            v = read_json(vjson)
            if not v:
                findings.append(Finding("parse", page, f"could not read {vjson}"))
                continue
            vid = vjson.parent.name
            vtype = ((v.get("visual") or {}).get("visualType")) or "?"
            # isHidden lives at the ROOT of visual.json. A hidden visual can't lie on
            # screen: it's not shown.
            if v.get("isHidden"):
                continue
            measures, columns = visual_refs(v)
            tag = f"{vtype} {vid}"

            if "filters" in active:
                for col, values, repeated, grouped in multi_value_filters(v):
                    if grouped and not repeated:
                        continue  # the row context pins a single value
                    culprits = sorted(m for m in measures
                                      if any(c == col for _, c in sensitive.get(m, ())))
                    if model_dir and not culprits and not repeated:
                        continue  # no measure of this visual depends on that column
                    note = " (REPEATED VALUES)" if repeated else ""
                    if culprits:
                        why = (f"{culprits} resolve(s) the row with that column -> "
                               "it computes a different value from the one it shows")
                    elif repeated:
                        why = "a repeated value gives away a hand-edited filter"
                    else:
                        why = "without the model I can't confirm which measure depends on it"
                    findings.append(Finding("filters", page,
                                            f"{tag}: {col} IN {values}{note}", why))
                for col, values, _, _ in multi_value_filters(v):
                    if types.get(col) in {"int64", "double", "decimal"} and any(
                            not re.fullmatch(r"-?\d+(\.\d+)?[LD]?", x) for x in values):
                        findings.append(Finding(
                            "filters", page,
                            f"{tag}: {col} is {types[col]} but is compared against text {values}",
                            "corrupted filter: it will never match"))

            if "family" in active and family:
                own = defaultdict(set)
                for m in measures:
                    hit = re.search(family, m)
                    if hit:
                        own[hit.group(1)].add(m)
                if model_dir:
                    own = {k: vv for k, vv in own.items() if any(m in sensitive for m in vv)}
                    # Grouping by the stage table pins the STAGE of each row, never the
                    # SEGMENT (the family suffix encodes the population). The SAME
                    # metric with different suffixes side by side is a deliberate
                    # comparison; DIFFERENT metrics cross one segment with another.
                    sens_tables = {t for m in measures for t, _ in sensitive.get(m, ())}
                    by_stage = bool(sens_tables & {t for t, _ in projected_columns(v) if t})
                    roots = {re.sub(family, "", m).strip() for vv in own.values() for m in vv}
                    if by_stage and len(roots) <= 1:
                        own = {}
                if len(own) > 1:
                    detail = "; ".join(f"{k}: {sorted(vv)}" for k, vv in sorted(own.items()))
                    findings.append(Finding(
                        "family", page, f"{tag} mixes {len(own)} families -> {detail}",
                        "it crosses the figure of one segment with another's "
                        "(e.g. the volume of one population and the time of another)"))

            if "refs" in active and model_dir:
                for m in sorted(measures - m_measures - m_columns):
                    findings.append(Finding("refs", page,
                                            f"{tag}: measure {m!r} does not exist in the model"))
                for c in sorted(columns - m_columns - m_measures):
                    findings.append(Finding("refs", page,
                                            f"{tag}: column {c!r} does not exist in the model"))

            if "geometry" in active:
                pos = v.get("position") or {}
                x, y = pos.get("x", 0), pos.get("y", 0)
                w, h = pos.get("width", 0), pos.get("height", 0)
                # With parentGroupName the coordinates are relative to the group.
                if not v.get("parentGroupName"):
                    if x < 0 or y < 0 or x + w > width + 1 or y + h > height + 1:
                        findings.append(Finding(
                            "geometry", page,
                            f"{tag}: ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f} is outside "
                            f"the {width}x{height} canvas"))
                    if vtype not in DECORATIVE:
                        geo.append((vid, vtype, x, y, w, h))

        if "geometry" in active:
            for i in range(len(geo)):
                for j in range(i + 1, len(geo)):
                    a, b = geo[i], geo[j]
                    ix = max(0, min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2]))
                    iy = max(0, min(a[3] + a[5], b[3] + b[5]) - max(a[3], b[3]))
                    area_a, area_b = a[4] * a[5], b[4] * b[5]
                    smaller = min(area_a, area_b) or 1
                    covers = ix * iy / smaller
                    # A small card fully inside a big chart is deliberate layout. What
                    # gives away a hidden duplicate is one covering another of SIMILAR size.
                    similar = smaller / (max(area_a, area_b) or 1) >= 0.8
                    if covers >= 0.95 and similar and (a[0] in bookmarks or b[0] in bookmarks):
                        continue  # toggled by a bookmark, not a duplicate
                    if covers >= 0.95 and similar:
                        findings.append(Finding(
                            "geometry", page,
                            f"{a[1]} {a[0]} and {b[1]} {b[0]} overlap {covers:.0%}",
                            "same place and same size: the one below can't be seen, "
                            "usually a leftover duplicate"))

    if "tmdl" in active and model_dir:
        for tmdl, line in orphan_descriptions(model_dir):
            findings.append(Finding(
                "tmdl", None, f"{tmdl.name}:{line} /// description followed by a blank line",
                "Desktop refuses to open the WHOLE project (InvalidLineType: Empty)"))

    return findings
