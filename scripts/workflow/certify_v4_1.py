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
]

PR_TEMPLATE_REQUIRED_STRINGS = [
    "## Summary",
    "## Severity",
    "## Validation",
    "## AI usage note",
    "## Self-audit",
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
        ok("PR template anchors + AI usage note present")

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

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
