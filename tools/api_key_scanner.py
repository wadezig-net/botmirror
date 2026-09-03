#!/usr/bin/env python3
"""
Hermes API Key Finder — Bot Integration Module.

Integrates api_key_finder.py into the Hermes bot as a /scan command handler.
Usage from bot context:
    from tools.api_key_scanner import scan_for_secrets, format_scan_report

    results = scan_for_secrets("/path/to/scan")
    msg = format_scan_report(results)
    # send msg via bot
"""

import os
import sys
import json
import re
import time
from pathlib import Path
from collections import defaultdict

# Ensure tools dir is importable
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from api_key_finder import (  # noqa: E402
    APIKeyFinder, ScanReport, ScanResult, DEFAULT_PATTERNS,
    SKIP_EXTENSIONS, SKIP_PATHS,
)


def scan_for_secrets(target_path, extensions=None, recursive=True, timeout=120):
    """
    Scan a path for secrets and return structured results.

    Args:
        target_path: str — file, directory, or git repo path
        extensions: list[str] | None — file extensions to include (e.g. ['.py', '.js'])
        recursive: bool — recursive directory scan
        timeout: int — max seconds for the scan

    Returns:
        dict — {
            "status": "ok" | "error",
            "path": str,
            "findings": [ {file, line, match, type} ],
            "summary": {total, files_hit, by_type},
            "duration_seconds": float,
        }
    """
    start = time.time()
    result = {
        "status": "error",
        "path": target_path,
        "findings": [],
        "summary": {"total": 0, "files_hit": 0, "by_type": {}},
        "duration_seconds": 0,
        "error": None,
    }

    if not os.path.exists(target_path):
        result["error"] = f"Path not found: {target_path}"
        return result

    try:
        scanner = APIKeyFinder(verbose=False)
        report = ScanReport(target_path)

        if os.path.isfile(target_path):
            scanner.scan_file(target_path, report)
        elif os.path.isdir(target_path):
            # Filter extensions if provided
            ext_set = set(extensions) if extensions else None
            if ext_set:
                # Temporarily patch scan_directory to use extensions
                original_walk = scanner.scan_directory
                def scan_with_ext(path, *args, **kwargs):
                    # We use the normal scan but filter results by extension
                    return original_walk(path, *args, **kwargs)
                scanner.scan_directory(target_path, recursive=recursive, extensions=ext_set, report=report)
            else:
                scanner.scan_directory(target_path, recursive=recursive, report=report)

        result["status"] = "ok"
        result["findings"] = [
            {"file": r.file_path, "line": r.line_num, "match": r.match_text[:80], "type": r.pattern_type}
            for r in report.results
        ]
        result["summary"] = {
            "total": len(report.results),
            "files_hit": len(set(r.file_path for r in report.results)),
            "by_type": dict(report.stats),
        }
        result["duration_seconds"] = round(time.time() - start, 2)
    except Exception as e:
        result["error"] = str(e)
        result["duration_seconds"] = round(time.time() - start, 2)

    return result


def format_scan_report(data):
    """Format scan results into a Telegram-friendly message string."""
    if data["status"] == "error":
        return f"❌ Scan error: {data['error']}"

    total = data["summary"]["total"]
    if total == 0:
        return (
            "✅ No secrets or API keys found!\n"
            f"📁 Scanned: `{data['path']}`\n"
            f"⏱ Duration: {data['duration_seconds']}s"
        )

    lines = [
        f"🔍 **Secret Scan Results**\n",
        f"📁 Path: `{data['path']}`\n",
        f"⚠️ **{total} finding(s)** in {data['summary']['files_hit']} file(s)\n",
        f"⏱ Duration: {data['duration_seconds']}s\n",
        f"{'─' * 30}",
    ]

    # Group by type
    by_type = defaultdict(list)
    for f in data["findings"]:
        by_type[f["type"]].append(f)

    for ftype, findings in sorted(by_type.items()):
        lines.append(f"📌 **{ftype}** ({len(findings)})")
        for f in findings[:10]:  # limit per type to avoid long messages
            short_file = f["file"].split("/")[-1] if "/" in f["file"] else f["file"]
            lines.append(f"   `{short_file}:{f['line']}` → `{f['match'][:40]}`")
        if len(findings) > 10:
            lines.append(f"   ... and {len(findings) - 10} more")
        lines.append("")

    lines.append(f"{'─' * 30}")
    lines.append("⚠️ **Do NOT share these findings publicly!**")
    lines.append("Rotate exposed keys immediately via your provider dashboard.")
    lines.append("Use `api_key_finder.py --config-only` to customize scan patterns.")

    return "\n".join(lines)


def scan_telegram_message(text):
    """Quick check: scan a Telegram message text for leaked credentials.
    Returns True if suspicious pattern found."""
    patterns = [
        r"sk-[A-Za-z0-9_\-]{20,}",
        r"ghp_[A-Za-z0-9_]{20,}",
        r"xoxb-[A-Za-z0-9_\-]{10,}",
        r"(?:sk|rk)_test_[A-Za-z0-9_]{20,}",
        r"(?:sk|rk)_live_[A-Za-z0-9_]{20,}",
        r"[\d]{6,}:[A-Za-z0-9_\-]{20,}",
    ]
    for pat in patterns:
        if re.search(pat, text):
            return True
    return False


def print_scan_results(data):
    """Print scan results to stdout (for debugging / direct CLI use)."""
    print(json.dumps(data, indent=2, default=str))


# --- CLI entry point (for testing) ---
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermes API Key Scanner — bot integration")
    parser.add_argument("path", help="Path to scan")
    parser.add_argument("--extensions", "-e", nargs="+", help="File extensions to scan")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--telegram", action="store_true", help="Test message for leaked creds")
    args = parser.parse_args()

    if args.telegram:
        print(f"Contains leaked creds: {scan_telegram_message(args.path)}")
        sys.exit(0)

    data = scan_for_secrets(args.path, extensions=args.extensions)
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_scan_report(data))