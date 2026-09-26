"""Wrappers around Microsoft's own validators.

- TMDL: `Microsoft.AnalysisServices` TmdlSerializer (.NET 8), built once into a cache dir.
- PBIR: `@microsoft/powerbi-report-authoring-cli` (npm), whose one-line JSON output is
  grouped by code with each page resolved to its visible name.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

CACHE = Path(os.environ.get("PBIP_GUARDRAILS_CACHE", Path.home() / ".cache" / "pbip-guardrails"))


class MissingTool(RuntimeError):
    """A required external tool is not installed."""


def _gate_dll() -> Path:
    """Build tmdl-gate once into the cache (rebuilt only if its sources change)."""
    if not shutil.which("dotnet"):
        raise MissingTool("dotnet not found. Install the .NET 8 SDK to run the TMDL gate.")
    src = resources.files("pbip_guardrails") / "tmdl_gate"
    files = {name: (src / name).read_bytes() for name in ("Program.cs", "app.csproj")}
    digest = hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:12]
    work = CACHE / f"tmdl-gate-{digest}"
    dll = work / "out" / "app.dll"
    if dll.exists():
        return dll
    work.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (work / name).write_bytes(data)
    # First build downloads the Analysis Services package; after that it takes seconds.
    proc = subprocess.run(["dotnet", "build", str(work), "-c", "Release", "-o", str(work / "out"),
                           "--nologo", "-v", "q"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"tmdl-gate build failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-800:]}")
    return dll


def tmdl_gate(model_dir: Path) -> tuple[int, str]:
    """Parse <model>/definition with the official serializer. Returns (exit code, output)."""
    definition = model_dir / "definition" if (model_dir / "definition").is_dir() else model_dir
    proc = subprocess.run(["dotnet", str(_gate_dll()), str(definition)],
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).rstrip()


def _run_pbir_cli(report_dir: Path, offline: bool) -> dict:
    exe = shutil.which("powerbi-report-author")
    if not exe:
        raise MissingTool("powerbi-report-author not found. Install it with: "
                          "npm install -g @microsoft/powerbi-report-authoring-cli")
    cmd = [exe, "validate", str(report_dir)] + (["--no-schema"] if offline else [])
    # It exits != 0 when it finds errors: that is NOT an execution failure.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            return raw.get("data", raw)
    raise RuntimeError(f"could not parse the CLI output\n{proc.stdout[:400]}\n{proc.stderr[:400]}")


def _groups(diagnostics) -> dict:
    """Normalize `diagnostics`: a dict by code (or a list, in other CLI versions)."""
    groups = {}
    if isinstance(diagnostics, dict):
        for code, body in diagnostics.items():
            groups[code] = ((body or {}).get("severity", ""), (body or {}).get("items") or [])
    elif isinstance(diagnostics, list):
        for d in diagnostics:
            sev, items = groups.setdefault(d.get("code", "NO_CODE"), (d.get("severity", ""), []))
            items.append(d)
    return groups


def pbir_validate(report_dir: Path, offline: bool = False, errors_only: bool = False,
                  max_examples: int = 4) -> tuple[int, str]:
    """Run the official PBIR validator and group its output. Returns (exit code, text)."""
    data = _run_pbir_cli(report_dir, offline)
    names = {}
    for page_json in sorted(report_dir.glob("definition/pages/*/page.json")):
        try:
            names[page_json.parent.name] = json.loads(
                page_json.read_text(encoding="utf-8-sig")).get("displayName", "")
        except (OSError, json.JSONDecodeError):
            continue

    def page_of(item):
        path = str(item.get("file") or item.get("path") or item.get("message") or "")
        pid = next((k for k in names if k in path), None)
        return names.get(pid) if pid else None

    has_error, out = False, []
    ordered = sorted(_groups(data.get("diagnostics")).items(),
                     key=lambda kv: (kv[1][0] != "error", -len(kv[1][1])))
    for code, (sev, items) in ordered:
        if errors_only and sev != "error":
            continue
        has_error |= sev == "error"
        out.append(f"[{len(items):4d}] {sev.upper():7s} {code}")
        for item in (items if max_examples == 0 else items[:max_examples]):
            page = page_of(item)
            # The message carries the absolute path glued at the end: noise.
            msg = (item.get("message") or "").split(": /")[0].strip()
            out.append(f"        {repr(page) + ' ' if page else ''}{msg[:170]}")
        if max_examples and len(items) > max_examples:
            out.append(f"        ... and {len(items) - max_examples} more")
    head = (f"result={data.get('result', '?')}  errors={data.get('errorCount', '?')}  "
            f"warnings={data.get('warningCount', '?')}")
    return (1 if has_error else 0), "\n".join([head, *out])
