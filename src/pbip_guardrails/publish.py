"""Publish PBIP items from Linux, without Power BI Desktop, on a Pro license.

Why it works on Pro: fabric-cicd lists Report and SemanticModel in
NO_ASSIGNED_CAPACITY_REQUIRED and skips the capacity check when those are the only
item types in scope. A Pro workspace (no Fabric/Premium capacity) is enough.

Safety rails, each one learned the hard way:
- `safe_copy` publishes a renamed copy (prefix ZZ-) so the first try never overwrites
  the real report. fabric-cicd matches items by NAME, so a new name always CREATES.
- `publish` never calls unpublish_all_orphan_items: in a shared workspace it would
  delete every item that is not in your folder.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"


def _git_files(repo: Path, src: Path) -> list[Path]:
    """Files git knows about (tracked + untracked-not-ignored) under `src`.

    Why git and not a plain copy: the ignored .pbi/cache.abf can weigh hundreds of MB.
    Why --others: with tracked files only, a visual you just added and haven't
    committed silently never reaches the copy.
    """
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                          str(src.relative_to(repo))],
                         cwd=repo, capture_output=True, text=True, check=True).stdout
    return [repo / line for line in out.splitlines() if line]


def safe_copy(project_dir: Path, name: str, new_name: str, dest: Path,
              unhide_pages: bool = False) -> Path:
    """Copy <project_dir>/<name>.{Report,SemanticModel} to <dest> as a NEW item.

    Changes displayName and logicalId and repoints the report to its copied model.
    Use an unmistakable prefix (ZZ-): it sorts last and reads as disposable. Don't
    use names like "Sales2", which look like an official successor.
    """
    repo = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=project_dir,
                               capture_output=True, text=True).stdout.strip() or project_dir)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for kind in ("Report", "SemanticModel"):
        src = project_dir / f"{name}.{kind}"
        if not src.is_dir():
            continue
        dst = dest / f"{new_name}.{kind}"
        in_git = (repo / ".git").exists()
        files = _git_files(repo, src) if in_git else [p for p in src.rglob("*")
                                                      if p.is_file() and ".pbi" not in p.parts]
        for f in files:
            target = dst / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        platform = dst / ".platform"
        if platform.exists():
            meta = json.loads(platform.read_text(encoding="utf-8-sig"))
            meta["metadata"]["displayName"] = new_name
            meta["config"]["logicalId"] = str(uuid.uuid4())  # a new item, not the original
            platform.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    pbir = dest / f"{new_name}.Report" / "definition.pbir"
    if pbir.exists():
        ref = json.loads(pbir.read_text(encoding="utf-8-sig"))
        ref["datasetReference"]["byPath"]["path"] = f"../{new_name}.SemanticModel"
        pbir.write_text(json.dumps(ref, indent=2), encoding="utf-8")
    if unhide_pages:
        for pg in glob.glob(str(dest / f"{new_name}.Report/definition/pages/*/page.json")):
            data = json.loads(Path(pg).read_text(encoding="utf-8-sig"))
            if data.pop("visibility", None):
                Path(pg).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def publish(folder: Path, workspace_id: str, item_types=("Report", "SemanticModel"),
            client_secret_env: bool = False, credential=None) -> None:
    """Publish EVERYTHING under `folder` to `workspace_id`.

    Interactive by default (device code: open the URL, paste the code). With
    `client_secret_env`, uses a service principal from AZURE_TENANT_ID /
    AZURE_CLIENT_ID / AZURE_CLIENT_SECRET for unattended CI. Pass `credential` to reuse
    an azure-identity credential you already have (e.g. to call the REST API afterwards).

    After publishing, force a refresh: calculated tables (DATATABLE, etc.) are created
    empty, and a brand-new dataset has no data until its first refresh.
    """
    # Corporate proxies that intercept TLS (e.g. Cloudflare Gateway) break certifi;
    # the system bundle usually has their root.
    if os.path.exists(SYSTEM_CA):
        os.environ.setdefault("REQUESTS_CA_BUNDLE", SYSTEM_CA)
        os.environ.setdefault("SSL_CERT_FILE", SYSTEM_CA)

    from fabric_cicd import FabricWorkspace, publish_all_items  # heavy, optional extra

    if credential is not None:
        cred = credential
    elif client_secret_env:
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"],
                                      os.environ["AZURE_CLIENT_ID"],
                                      os.environ["AZURE_CLIENT_SECRET"])
    else:
        from azure.identity import DeviceCodeCredential
        cred = DeviceCodeCredential(prompt_callback=lambda uri, code, _exp: print(
            f"\n>>> LOGIN: open {uri} and enter the code {code}\n", flush=True))
    ws = FabricWorkspace(repository_directory=str(folder), workspace_id=workspace_id,
                         item_type_in_scope=list(item_types), token_credential=cred)
    publish_all_items(ws)
