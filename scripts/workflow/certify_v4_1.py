#!/usr/bin/env python3
"""Lightweight AI workflow certification checks (v4.1).

Fail-fast script for workflow/process guardrails only.
"""
from __future__ import annotations
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = [
    ".github/pull_request_template.md",
    ".claude/settings.json",
    ".claude/hooks/ai_os_advisory.py",
    "docs/ai/AI_REPO_OPERATING_SYSTEM.md",
    "docs/ai/PROMPT_ENGINEERING_STANDARD.md",
    "docs/ai/AI_USAGE_TRACKING.md",
]

PR_TEMPLATE_REQUIRED_STRINGS = [
    "## Summary",
    "## Severity",
    "## Validation",
    "## AI usage note",
    "## Self-audit",
    "Repository PR template used exactly: Yes/No",
    "Scope stayed workflow-only (no product code): Yes/No",
]

SETTINGS_RULES = {
    "deny_env_reads": ["Read(./.env)", "Read(./.env.*)"],
    "has_stop_hook": True,
    "has_post_tool_use_hook": True,
}

HOOK_REQUIRED_STRINGS = [
    "intentionally non-blocking",
    "return 0",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"PASS: {msg}")


def main() -> int:
    failed = False

    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.exists():
            fail(f"required file missing: {rel}")
            failed = True
        else:
            ok(f"required file present: {rel}")

    pr_template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    for needle in PR_TEMPLATE_REQUIRED_STRINGS:
        if needle not in pr_template:
            fail(f"PR template missing required field: {needle}")
            failed = True
    if not failed:
        ok("PR template required fields found")

    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    deny = settings.get("permissions", {}).get("deny", [])
    for env_read in SETTINGS_RULES["deny_env_reads"]:
        if env_read not in deny:
            fail(f"settings deny list missing: {env_read}")
            failed = True
    hooks = settings.get("hooks", {})
    if SETTINGS_RULES["has_stop_hook"] and "Stop" not in hooks:
        fail("settings missing Stop hook")
        failed = True
    if SETTINGS_RULES["has_post_tool_use_hook"] and "PostToolUse" not in hooks:
        fail("settings missing PostToolUse hook")
        failed = True
    if not failed:
        ok("settings hook + permission checks passed")

    hook_text = (ROOT / ".claude/hooks/ai_os_advisory.py").read_text(encoding="utf-8").lower()
    for needle in HOOK_REQUIRED_STRINGS:
        if needle not in hook_text:
            fail(f"advisory hook invariant missing: {needle}")
            failed = True
    if "raise systemexit(main())" not in hook_text:
        fail("advisory hook entrypoint missing")
        failed = True
    if not failed:
        ok("advisory hook safety checks passed")

    handoff = (ROOT / "docs/ai/HANDOFF.md").read_text(encoding="utf-8")
    if len(handoff.splitlines()) > 500:
        fail("HANDOFF.md exceeds ~500 line guidance")
        failed = True
    else:
        ok("HANDOFF.md line-count within guidance")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
