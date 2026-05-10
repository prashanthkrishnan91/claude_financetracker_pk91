#!/usr/bin/env python3
"""Repo hygiene audit — read-only.

Scans the working tree for legacy patterns, oversized progress logs,
and skipped/xfail tests without an explanatory reason. Prints a report
to stdout. Never writes, never deletes.

Run:
    python3 scripts/repo_hygiene/audit_repo_hygiene.py

Rules / philosophy: docs/ai/REPO_HYGIENE.md.

Exit code:
    0 — script ran to completion (findings or no findings).
    1 — script could not read the repo root.

Findings alone do not fail this script. The maintainer reviews the report
before opening a PR.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

SCAN_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".md",
    ".json",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
    ".sql",
    ".sh",
}

# Directories to skip outright. Note: dot-directories are *not* skipped by
# default — repo-control dirs like `.github` and `.claude` can hide stale
# deployment / workflow references. Only skip noisy/generated dot-dirs here.
SKIP_DIRS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    ".kiro",
    ".claude-flow",
    "node_modules",
    "out",
    "__pycache__",
    "venv",
    "env",
    "dist",
    "build",
    "graphify-out",
}

# Files / paths intentionally allowed to mention legacy strings
# (e.g. the hygiene doc and this script itself).
ALLOW_LEGACY_FILES = {
    "docs/ai/REPO_HYGIENE.md",
    "scripts/repo_hygiene/audit_repo_hygiene.py",
    "v2/progress_log.md",         # may reference cleanup history
    "docs/ai/HANDOFF.md",         # may reference cleanup history
    "docs/ai/MISS_LEDGER.md",     # workflow-miss history may reference legacy
}

# Substrings whose match is *not* a legacy v1-Streamlit signal.
# Keep this list short. Each entry is a substring; if it appears in the
# matched line, the finding is suppressed.
LEGACY_ALLOWLIST_SUBSTRINGS = (
    # Phrases that explicitly document the cleanup itself.
    "has been retired",                 # cleanup-history phrasing
    "has been removed",                 # cleanup-history phrasing
    "was removed",                      # cleanup-history phrasing
    "no longer",                        # cleanup-history phrasing
    "/api/v1",                          # live FastAPI namespace
    "api_v1",                           # ditto
    "/auth/v1/.well-known",             # Supabase Auth provider URL
    "decision_policy_v1",               # active Intel v3 policy module
    "data_truth_v1",                    # active Intel v3 data-truth module
    "valuation_context_adapter_v1",     # active Intel v3 module
    "valuation_input_verification_v1",  # active Intel v3 module
    "priceband_visible_language_v1",    # active Intel v3 module
    "sec_metric_truth_adapter_v1",      # active Intel v3 module
    "eps_payload_extractor_v1",         # active Intel v3 module
    "ticker_fy_eps_gap_classifier_v1",  # active Intel v3 module
    "price_sector_source_resolution_v1", # active Intel v3 module
    "research_artifact_store_v1",       # active migration / spec name
    "Plan_v1.pdf",                      # historical artifact PDF
    "compact_v1",                       # reasoning schema label
    "human_v2",                         # reasoning schema label
    "artifact.v1",                      # artifact schema_version literal
    "INTEL_V3_RESEARCH_ARTIFACT_STORE_V1",  # spec doc filename
    "Intel_v3",                         # active Intel v3 references
    "Intel v3",                         # active Intel v3 references
    "Intel v4",                         # forward roadmap references
    "Intel v2",                         # historical decisions intentionally referenced
    "narrative_contract_version",       # version label in contract payload
)

# Patterns that strongly indicate retired Streamlit / v1 product surfaces.
LEGACY_PATTERNS = (
    ("streamlit", "Reference to retired Streamlit runtime"),
    ("portfolio war room", "Reference to retired v1 product name"),
    ("seed_v1_positions", "Reference to removed migration helper"),
    ("seed-v1", "Reference to removed seed-v1 router endpoint"),
    ("migration_service", "Reference to removed migration_service module"),
    # Stale historical-v1 wording. These are narrow English phrases that
    # only appear when documentation still talks about the retired product.
    # They will not match `/api/v1/...`, `_v1` policy modules, schema
    # versions, or `Plan_v1.pdf`.
    ("carried from v1", "Stale 'carried from v1' historical wording"),
    ("from v1", "Stale 'from v1' historical wording"),
    ("v1 migration", "Stale 'v1 migration' wording"),
    ("v1 bootstrap", "Stale 'v1 bootstrap' wording"),
    ("v1 users", "Stale 'v1 users' wording"),
    ("v1-specific", "Stale 'v1-specific' wording"),
)

# Stale paths whose mere presence is a finding.
STALE_PATHS = (
    ("v1", "Retired Streamlit v1 product directory"),
    ("App.py", "Root Streamlit Cloud entry shim"),
    (".streamlit", "Streamlit runtime config dir"),
    ("v2/progress_log_archive.md", "Stale progress log archive (consolidate into progress_log.md)"),
)

# Soft cap for v2/progress_log.md.
PROGRESS_LOG_PATH = os.path.join("v2", "progress_log.md")
PROGRESS_LOG_SOFT_CAP_LINES = 250

# Conditional skip patterns we treat as defensive guards (not stale skips).
CONDITIONAL_SKIP_HINTS = (
    "not found",
    "not available",
    "not installed",
    "skip(",  # already inside a conditional block
    "if not os.path",
    "if not has",
)


@dataclass
class Finding:
    severity: str          # "info" | "warn"
    category: str
    path: str
    line_no: int | None
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _iter_repo_files() -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        # Skip only the explicit SKIP_DIRS list (which already enumerates
        # the noisy dot-dirs we care about). `.github` and `.claude` are
        # intentionally NOT skipped so workflow / config files are scanned.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SCAN_EXTENSIONS:
                out.append(os.path.join(dirpath, fn))
    return out


def _file_lines(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except (OSError, UnicodeDecodeError):
        return None


def _line_is_allowlisted(line: str) -> bool:
    return any(sub in line for sub in LEGACY_ALLOWLIST_SUBSTRINGS)


# ── Checks ───────────────────────────────────────────────────────────────────

def check_legacy_strings(report: Report) -> None:
    for abs_path in _iter_repo_files():
        rel = _rel(abs_path)
        if rel in ALLOW_LEGACY_FILES:
            continue
        lines = _file_lines(abs_path)
        if lines is None:
            continue
        report.files_scanned += 1
        for i, raw in enumerate(lines, start=1):
            lower = raw.lower()
            if _line_is_allowlisted(raw):
                continue
            for needle, desc in LEGACY_PATTERNS:
                if needle in lower:
                    report.add(Finding(
                        severity="warn",
                        category="legacy_string",
                        path=rel,
                        line_no=i,
                        message=f"{desc}: matched '{needle}'",
                    ))


def check_stale_paths(report: Report) -> None:
    for rel, desc in STALE_PATHS:
        abs_path = os.path.join(REPO_ROOT, rel)
        if os.path.exists(abs_path):
            report.add(Finding(
                severity="warn",
                category="stale_path",
                path=rel,
                line_no=None,
                message=desc,
            ))


def check_progress_log_size(report: Report) -> None:
    abs_path = os.path.join(REPO_ROOT, PROGRESS_LOG_PATH)
    lines = _file_lines(abs_path)
    if lines is None:
        report.add(Finding(
            severity="info",
            category="progress_log",
            path=PROGRESS_LOG_PATH,
            line_no=None,
            message="progress_log.md not found",
        ))
        return
    n = len(lines)
    if n > PROGRESS_LOG_SOFT_CAP_LINES:
        report.add(Finding(
            severity="warn",
            category="progress_log",
            path=PROGRESS_LOG_PATH,
            line_no=None,
            message=(
                f"progress_log.md has {n} lines (soft cap "
                f"{PROGRESS_LOG_SOFT_CAP_LINES}); compact older entries"
            ),
        ))


_SKIP_RE = re.compile(r"(@pytest\.mark\.(skip|xfail)|pytest\.skip\()")


def check_skipped_tests(report: Report) -> None:
    tests_root = os.path.join(REPO_ROOT, "v2", "backend", "tests")
    if not os.path.isdir(tests_root):
        return
    # Recurse so future nested test directories are covered.
    for dirpath, dirnames, filenames in os.walk(tests_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fn)
            lines = _file_lines(abs_path)
            if lines is None:
                continue
            for i, raw in enumerate(lines, start=1):
                if not _SKIP_RE.search(raw):
                    continue
                # Look at a small window above the match for a conditional
                # guard (defensive skip), which we treat as legitimate.
                window_start = max(0, i - 4)
                window = "".join(lines[window_start:i]).lower()
                if any(hint in window for hint in CONDITIONAL_SKIP_HINTS):
                    continue
                report.add(Finding(
                    severity="info",
                    category="skipped_test",
                    path=_rel(abs_path),
                    line_no=i,
                    message="skip/xfail without an obvious conditional guard",
                ))


# Async test antipattern — calling asyncio.get_event_loop() inside a sync
# test runs against an event loop that pytest-asyncio may have closed for
# an earlier async test. Result: order-dependent failures of the form
# "RuntimeError: There is no current event loop in thread 'MainThread'".
# Tests should use ``asyncio.run(...)`` (or an explicit
# ``asyncio.new_event_loop()`` / ``asyncio.set_event_loop()`` setup).
_ASYNCIO_GET_LOOP_RE = re.compile(r"asyncio\.get_event_loop\s*\(")


def check_async_test_antipatterns(report: Report) -> None:
    tests_root = os.path.join(REPO_ROOT, "v2", "backend", "tests")
    if not os.path.isdir(tests_root):
        return
    for dirpath, dirnames, filenames in os.walk(tests_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fn)
            lines = _file_lines(abs_path)
            if lines is None:
                continue
            for i, raw in enumerate(lines, start=1):
                if _ASYNCIO_GET_LOOP_RE.search(raw):
                    report.add(Finding(
                        severity="warn",
                        category="async_test_antipattern",
                        path=_rel(abs_path),
                        line_no=i,
                        message=(
                            "asyncio.get_event_loop() in a test — prefer "
                            "asyncio.run(...) to avoid order-dependent "
                            "'no current event loop' failures"
                        ),
                    ))


# Frontend skipped tests — describe.skip / it.skip / test.skip / .todo
_FE_SKIP_RE = re.compile(r"(describe|it|test)\.(skip|todo)\b")


def check_frontend_skipped_tests(report: Report) -> None:
    fe_root = os.path.join(REPO_ROOT, "v2", "frontend", "src")
    if not os.path.isdir(fe_root):
        return
    for dirpath, dirnames, filenames in os.walk(fe_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not (fn.endswith(".test.ts") or fn.endswith(".test.tsx")):
                continue
            abs_path = os.path.join(dirpath, fn)
            lines = _file_lines(abs_path)
            if lines is None:
                continue
            for i, raw in enumerate(lines, start=1):
                if _FE_SKIP_RE.search(raw):
                    report.add(Finding(
                        severity="info",
                        category="skipped_test",
                        path=_rel(abs_path),
                        line_no=i,
                        message="frontend test marked skip/todo",
                    ))


# ── Reporting ────────────────────────────────────────────────────────────────

def _fmt_finding(f: Finding) -> str:
    where = f"{f.path}:{f.line_no}" if f.line_no else f.path
    return f"  [{f.severity}] {f.category} — {where} — {f.message}"


def _summary(report: Report) -> int:
    by_cat: dict[str, int] = {}
    for f in report.findings:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
    print(f"\nScanned {report.files_scanned} files. "
          f"Findings: {len(report.findings)}.")
    for cat, n in sorted(by_cat.items()):
        print(f"  - {cat}: {n}")
    return len(report.findings)


def main() -> int:
    if not os.path.isdir(REPO_ROOT):
        print(f"ERROR: repo root not readable: {REPO_ROOT}", file=sys.stderr)
        return 1

    report = Report()
    check_stale_paths(report)
    check_progress_log_size(report)
    check_legacy_strings(report)
    check_skipped_tests(report)
    check_async_test_antipatterns(report)
    check_frontend_skipped_tests(report)

    if report.findings:
        print("Repo hygiene audit — findings:\n")
        for f in report.findings:
            print(_fmt_finding(f))
    else:
        print("Repo hygiene audit — no findings.")

    _summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
