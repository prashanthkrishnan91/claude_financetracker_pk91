#!/usr/bin/env python3
"""Lightweight AI workflow certification checks (v4.1)."""
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = [
    ".github/pull_request_template.md",
    ".claude/settings.json",
    ".claude/hooks/ai_os_advisory.py",
    "docs/ai/AI_REPO_OPERATING_SYSTEM.md",
    "docs/ai/USAGE_LEDGER.md",
]

PR_TEMPLATE_REQUIRED_STRINGS = [
    "## Summary",
    "## Severity",
    "## Validation",
    "## AI usage note",
    "## Self-audit",
    "Usage ledger updated",
]

PR_TEMPLATE_REQUIRED_SELF_AUDIT = [
    "Repository PR template used exactly: Yes/No",
    "Scope stayed workflow-only (no product code): Yes/No",
]

ENV_DENY_RULES = ["Read(./.env)", "Read(./.env.*)"]
HOOK_REQUIRED_STRINGS = ["intentionally non-blocking", "return 0", "raise SystemExit(main())"]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"PASS: {msg}")


def main() -> int:
    failed = False

    for rel in REQUIRED_FILES:
        if not (ROOT / rel).exists():
            fail(f"required file missing: {rel}")
            failed = True
        else:
            ok(f"required file present: {rel}")

    pr_template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    for needle in PR_TEMPLATE_REQUIRED_STRINGS + PR_TEMPLATE_REQUIRED_SELF_AUDIT:
        if needle not in pr_template:
            fail(f"PR template missing required anchor: {needle}")
            failed = True
    if not failed:
        ok("PR template anchors + AI usage note + ledger updated present")

    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    deny = settings.get("permissions", {}).get("deny", [])
    for env_rule in ENV_DENY_RULES:
        if env_rule not in deny:
            fail(f"settings deny list missing: {env_rule}")
            failed = True
    if not failed:
        ok("settings .env deny rules present")

    hooks_obj = settings.get("hooks", {})
    hooks_text = json.dumps(hooks_obj).lower()
    advisory_hook_referenced = ".claude/hooks/ai_os_advisory.py" in hooks_text
    if advisory_hook_referenced:
        hook_text = (ROOT / ".claude/hooks/ai_os_advisory.py").read_text(encoding="utf-8")
        for needle in HOOK_REQUIRED_STRINGS:
            if needle not in hook_text:
                fail(f"advisory hook safety invariant missing: {needle}")
                failed = True
        if not failed:
            ok("advisory hook safety invariants passed (hook is configured)")
    else:
        ok("advisory hook safety invariant check skipped (hook not configured)")

    # Usage ledger structural checks
    tracking_path = ROOT / "docs/ai/AI_USAGE_TRACKING.md"
    if not tracking_path.exists():
        fail("docs/ai/AI_USAGE_TRACKING.md missing")
        failed = True
    else:
        tracking_text = tracking_path.read_text(encoding="utf-8")
        if "USAGE_LEDGER.md" not in tracking_text:
            fail("docs/ai/AI_USAGE_TRACKING.md does not document USAGE_LEDGER.md (two-layer model missing)")
            failed = True
        else:
            ok("AI_USAGE_TRACKING.md documents USAGE_LEDGER.md")

    snapshot_path = ROOT / "scripts/ai/usage_snapshot.sh"
    if not snapshot_path.exists():
        fail("scripts/ai/usage_snapshot.sh missing")
        failed = True
    else:
        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        if "--append-ledger" not in snapshot_text:
            fail("scripts/ai/usage_snapshot.sh does not reference --append-ledger")
            failed = True
        else:
            ok("usage_snapshot.sh references ledger append behavior")

    gitignore_path = ROOT / ".gitignore"
    if not gitignore_path.exists():
        fail(".gitignore missing")
        failed = True
    else:
        gitignore_text = gitignore_path.read_text(encoding="utf-8")
        if ".ai/usage" not in gitignore_text:
            fail(".gitignore does not exclude .ai/usage/")
            failed = True
        else:
            ok(".gitignore excludes .ai/usage/")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
