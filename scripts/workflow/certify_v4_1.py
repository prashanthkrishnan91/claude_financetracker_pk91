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
    "scripts/workflow/ai_pr_readiness_check.py",
    ".github/workflows/ai-pr-readiness.yml",
]

PR_TEMPLATE_REQUIRED_STRINGS = [
    "## Summary",
    "## Severity",
    "## Validation",
    "## AI usage note",
    "## AI PR readiness",
    "## Self-audit",
    "Usage ledger row",
    "Waste classification",
]

PR_TEMPLATE_REQUIRED_SELF_AUDIT = [
    "Repository PR template used exactly: Yes/No",
    "Scope stayed within requested files/behavior: Yes/No",
]

USAGE_LEDGER_ANCHORS = ["Prompt ID", "Phase", "Linked PR", "Δ total", "Waste"]

SNAPSHOT_SCRIPT_ANCHORS = [
    "--append-ledger",
    "--prompt-id",
    "--phase",
    "--delta-from-baseline",
]

PROMPT_USAGE_FOOTER_ANCHORS = [
    "Usage ledger: If tooling exists",
    "Usage discipline: Keep discovery narrow",
]

CLAUDE_MD_READINESS_ANCHOR = "ai_pr_readiness_check.py"

USAGE_TRACKING_ENFORCEMENT_ANCHORS = [
    "Ledger claim enforcement",
    "ai_pr_readiness_check.py",
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
        ok("PR template anchors present (including AI PR readiness and updated self-audit)")

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

    tracking_path = ROOT / "docs/ai/AI_USAGE_TRACKING.md"
    if not tracking_path.exists():
        fail("docs/ai/AI_USAGE_TRACKING.md missing")
        failed = True
    else:
        tracking_text = tracking_path.read_text(encoding="utf-8")
        if "USAGE_LEDGER.md" not in tracking_text:
            fail("docs/ai/AI_USAGE_TRACKING.md does not document USAGE_LEDGER.md")
            failed = True
        else:
            for anchor in USAGE_TRACKING_ENFORCEMENT_ANCHORS:
                if anchor not in tracking_text:
                    fail(f"docs/ai/AI_USAGE_TRACKING.md missing enforcement anchor: {anchor}")
                    failed = True
            if not failed:
                ok("AI_USAGE_TRACKING.md documents USAGE_LEDGER.md with ledger claim enforcement")

    ledger_path = ROOT / "docs/ai/USAGE_LEDGER.md"
    if ledger_path.exists():
        ledger_text = ledger_path.read_text(encoding="utf-8")
        missing_cols = [col for col in USAGE_LEDGER_ANCHORS if col not in ledger_text]
        if missing_cols:
            for col in missing_cols:
                fail(f"USAGE_LEDGER.md missing required column anchor: {col}")
            failed = True
        else:
            ok("USAGE_LEDGER.md has required delta/prompt/waste columns")

    snapshot_path = ROOT / "scripts/ai/usage_snapshot.sh"
    if not snapshot_path.exists():
        fail("scripts/ai/usage_snapshot.sh missing")
        failed = True
    else:
        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        missing_flags = [flag for flag in SNAPSHOT_SCRIPT_ANCHORS if flag not in snapshot_text]
        if missing_flags:
            for flag in missing_flags:
                fail(f"usage_snapshot.sh missing required flag: {flag}")
            failed = True
        else:
            ok("usage_snapshot.sh references all required ledger flags")

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

    claude_path = ROOT / "CLAUDE.md"
    if not claude_path.exists():
        fail("CLAUDE.md missing")
        failed = True
    else:
        claude_text = claude_path.read_text(encoding="utf-8")
        if CLAUDE_MD_READINESS_ANCHOR not in claude_text:
            fail(f"CLAUDE.md does not reference readiness checker ({CLAUDE_MD_READINESS_ANCHOR})")
            failed = True
        else:
            ok("CLAUDE.md references ai_pr_readiness_check.py")

    for path_str in ["docs/ai/PROMPT_ENGINEERING_STANDARD.md", "docs/ai/PROMPT_LIBRARY.md"]:
        path = ROOT / path_str
        if path.exists():
            text = path.read_text(encoding="utf-8")
            for anchor in PROMPT_USAGE_FOOTER_ANCHORS:
                if anchor not in text:
                    fail(f"{path_str} missing usage footer anchor: {anchor}")
                    failed = True
            if not failed:
                ok(f"{path_str} contains required usage footer anchors")

    hook = ROOT / ".claude/hooks/ai_pr_readiness_stop.sh"
    gate_doc = ROOT / "docs/ai/AI_PR_READINESS_GATE.md"
    if not hook.exists() and not gate_doc.exists():
        fail("Neither ai_pr_readiness_stop.sh nor AI_PR_READINESS_GATE.md found")
        failed = True
    else:
        found = hook.name if hook.exists() else gate_doc.name
        ok(f"Readiness gate hook/doc present: {found}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
