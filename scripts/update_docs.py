#!/usr/bin/env python3
"""Auto-update progress_log.md with latest commit."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

def get_latest_commit():
    """Get latest commit hash, subject, and body."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H|%s|%b"],
            capture_output=True,
            text=True,
            check=True,
        )
        parts = result.stdout.strip().split("|", 2)
        if len(parts) >= 2:
            return {
                "hash": parts[0][:7],
                "subject": parts[1],
                "body": parts[2] if len(parts) > 2 else "",
            }
    except subprocess.CalledProcessError:
        return None
    return None

def update_progress_log(commit):
    """Append commit to progress_log.md under Recent Changes section."""
    if not commit:
        return False

    log_path = Path(__file__).parent.parent / "v2" / "progress_log.md"
    if not log_path.exists():
        return False

    content = log_path.read_text()

    # Check if Recent Changes section exists
    if "## Recent Changes" not in content:
        # Insert Recent Changes section after the header
        lines = content.split("\n")
        insert_idx = 2  # After "# v2 Progress Log\n\n---\n"
        recent_section = f"""## Recent Changes

### {commit['subject']}
- **Commit**: `{commit['hash']}`
- **Date**: {datetime.now().strftime('%B %d, %Y')}
{commit['body']}

---
"""
        lines.insert(insert_idx, recent_section)
        content = "\n".join(lines)
    else:
        # Append to existing Recent Changes section
        marker = "## Recent Changes\n"
        insert_pos = content.find(marker)
        if insert_pos != -1:
            insert_pos += len(marker)
            entry = f"""
### {commit['subject']}
- **Commit**: `{commit['hash']}`
- **Date**: {datetime.now().strftime('%B %d, %Y')}
{commit['body']}
"""
            content = content[:insert_pos] + entry + content[insert_pos:]

    log_path.write_text(content)
    return True

if __name__ == "__main__":
    commit = get_latest_commit()
    if commit:
        if update_progress_log(commit):
            print(f"✓ Updated progress_log.md: {commit['subject']}")
        else:
            print("⚠ Could not update progress_log.md", file=sys.stderr)
