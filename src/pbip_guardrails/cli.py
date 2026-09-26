"""`pbip` command line: lint, safe-copy, publish."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .checks import ALL_CHECKS, run_checks
from .external import MissingTool, pbir_validate, tmdl_gate


def find_items(path: Path) -> list[tuple[str, Path, Path | None]]:
    """Return [(name, report_dir, model_dir|None)] for a folder, a .pbip or a .Report."""
    path = path.resolve()
    if path.suffix == ".pbip":
        path = path.parent
    if path.name.endswith(".Report"):
        name = path.name[: -len(".Report")]
        model = path.parent / f"{name}.SemanticModel"
        return [(name, path, model if model.is_dir() else None)]
    items = []
    for report in sorted(path.rglob("*.Report")):
        if report.is_dir() and (report / "definition").is_dir():
            name = report.name[: -len(".Report")]
            model = report.parent / f"{name}.SemanticModel"
            items.append((name, report, model if model.is_dir() else None))
    return items


def cmd_lint(a) -> int:
    """Run the three layers over every report found; exit 1 if any layer fails."""
    items = find_items(Path(a.path))
    if not items:
        print(f"no *.Report folders found under {a.path}", file=sys.stderr)
        return 2
    layers = set(a.layers.split(","))
    failed = False
    for name, report, model in items:
        print(f"━━ {name}")
        if "checks" in layers:
            findings = run_checks(report, model, a.family, tuple(a.only.split(",")))
            print(f"  coherence checks: {len(findings)} finding(s)")
            for f in findings:
                where = f"'{f.page}' " if f.page else ""
                print(f"  {where}{f.render().strip()}")
            failed |= bool(findings)
        for layer, needs_model, run in (
                ("tmdl", True, lambda: tmdl_gate(model)),
                ("pbir", False, lambda: pbir_validate(report, a.offline, a.errors_only))):
            if layer not in layers or (needs_model and model is None):
                continue
            try:
                code, out = run()
            except MissingTool as exc:
                print(f"  {layer}: SKIPPED ({exc})")
                failed |= a.strict
                continue
            print(f"  {layer}: {'OK' if code == 0 else 'FAIL'}")
            print("\n".join("    " + line for line in out.splitlines()))
            failed |= code != 0
        print()
    return 1 if failed else 0


def cmd_safe_copy(a) -> int:
    """Prepare a renamed copy that publishes as a NEW item."""
    from .publish import safe_copy
    dest = safe_copy(Path(a.project), a.name, a.new_name, Path(a.dest), a.unhide_pages)
    print(f"copy ready in {dest} as '{a.new_name}' — publish it with: "
          f"pbip publish {dest} --workspace <id>")
    return 0


def cmd_publish(a) -> int:
    """Publish a folder after showing exactly what goes where."""
    folder = Path(a.folder)
    items = sorted(p.name for p in folder.iterdir() if p.is_dir())
    print(f">>> workspace : {a.workspace}\n>>> folder    : {folder}\n"
          f">>> items     : {', '.join(items) or '(none)'}\n"
          ">>> orphan items in the workspace are NOT deleted")
    if not items:
        return 1
    if not a.yes and input("Existing items with the same name get OVERWRITTEN "
                           "(no undo). Continue? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return 1
    from .publish import publish
    try:
        publish(folder, a.workspace, tuple(a.types.split(",")), a.service_principal)
    except Exception as exc:  # noqa: BLE001 — surface whatever fabric-cicd raised
        print(f">>> FAILED: {type(exc).__name__}: {str(exc)[:1500]}")
        return 1
    print(">>> PUBLISHED. Now force a refresh: calculated tables are created empty.")
    return 0


def main(argv=None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(prog="pbip", description="Lint and publish Power BI PBIP "
                                 "projects from Linux, without Power BI Desktop.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lint = sub.add_parser("lint", help="validate PBIP projects (exit 1 on findings)")
    lint.add_argument("path", help="folder, .pbip file or X.Report folder")
    lint.add_argument("--layers", default="checks,tmdl,pbir",
                      help="which layers to run (default: checks,tmdl,pbir)")
    lint.add_argument("--only", default=",".join(ALL_CHECKS),
                      help=f"coherence checks to run (default: {','.join(ALL_CHECKS)})")
    lint.add_argument("--family", help=r"regex with one group naming a measure family, "
                      r"e.g. 'Segment (\d+)'")
    lint.add_argument("--offline", action="store_true", help="pbir: skip remote JSON-schema")
    lint.add_argument("--errors-only", action="store_true", help="pbir: hide warnings")
    lint.add_argument("--strict", action="store_true",
                      help="fail when an external validator is not installed")
    lint.set_defaults(func=cmd_lint)

    cp = sub.add_parser("safe-copy", help="copy an item under a new name to publish as NEW")
    cp.add_argument("project", help="folder containing <name>.Report / <name>.SemanticModel")
    cp.add_argument("name")
    cp.add_argument("new_name", help="use an unmistakable prefix, e.g. ZZ-Sales-test")
    cp.add_argument("dest")
    cp.add_argument("--unhide-pages", action="store_true", help="unhide hidden pages in the copy")
    cp.set_defaults(func=cmd_safe_copy)

    pub = sub.add_parser("publish", help="publish every item in a folder (needs [publish] extra)")
    pub.add_argument("folder")
    pub.add_argument("--workspace", required=True, help="target workspace id")
    pub.add_argument("--types", default="Report,SemanticModel")
    pub.add_argument("--service-principal", action="store_true",
                     help="use AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET instead of device code")
    pub.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    pub.set_defaults(func=cmd_publish)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
