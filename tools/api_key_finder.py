#!/usr/bin/env python3
"""
API Key & Secret Scanner — Hermes Agent Tool
Scans files, directories, and git repos for leaked API keys, credentials, and secrets.

Usage:
    python tools/api_key_finder.py <path> [options]

Examples:
    python tools/api_key_finder.py /root/botmirror --recursive
    python tools/api_key_finder.py /root/botmirror --config-only
    python tools/api_key_finder.py /root/botmirror --git-history
    python tools/api_key_finder.py . --pattern API_KEY --json-out results.json
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# --- DEFAULT CONFIG ---
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key_patterns.json")

# --- DEFAULT PATTERNS (fallback if no config file) ---
DEFAULT_PATTERNS = {
    "generic": [
        (r"(?:api[_-]?key|apikey|api[_-]?secret|secret[_-]?key|secretkey)\s*[=:]\s*['\"]?([A-Za-z0-9_\\-]{16,})\"?", "API Key/Secret"),
        (r"(?:secret|private[_-]?key|privkey)\s*[=:]\s*['\"]?([A-Za-z0-9_/+=\\-]{20,})\"?", "Private/Secret Key"),
        (r"(?:access[_-]?key|accesskey|aws[_-]?access)\s*[=:]\s*['\"]?([A-Z0-9]{16,})\"?", "AWS Access Key"),
        (r"(?:secret[_-]?access|aws[_-]?secret)\s*[=:]\s*['\"]?([A-Za-z0-9_/+=]{20,})\"?", "AWS Secret Access"),
    ],
    "openai": [
        (r"(?:sk|sk-[A-Za-z0-9_]+)\s*[=:]\s*['\"]?([A-Za-z0-9_\\-]{20,})\"?", "OpenAI API Key"),
        (r"sk-[A-Za-z0-9_\\-]{20,}", "OpenAI Key (standalone)"),
    ],
    "anthropic": [
        (r"(?:claude[_-]?api|anthropic[_-]?api|x[- ]?api[- ]?key)\s*[=:]\s*['\"]?([A-Za-z0-9_\\-]{20,})\"?", "Anthropic API Key"),
        (r"x-api-key\s*[=:]\s*['\"]?([A-Za-z0-9_\\-]{20,})\"?", "Anthropic x-api-key"),
    ],
    "google": [
        (r"(?:google[_-]?api|gcp[_-]?key|google[_-]?cloud)\s*[=:]\s*['\"]?([A-Za-z0-9_\\-]{20,})\"?", "Google/GCP Key"),
        (r"(?:AIza)[A-Za-z0-9_\\-]{35}", "Google API Key (standalone)"),
    ],
    "github": [
        (r"(?:github[_-]?token|gh[_-]?token|github[_-]?access)\s*[=:]\s*['\"]?([A-Za-z0-9_]{20,})\"?", "GitHub Token"),
        (r"gh[pousr]_[A-Za-z0-9_]{20,}", "GitHub Token (standalone)"),
        (r"(?:github)[_ ]?[A-Za-z0-9_]{20,}", "GitHub Credential (generic)"),
    ],
    "stripe": [
        (r"(?:stripe[_-]?key|stripe[_-]?secret)\s*[=:]\s*['\"]?([A-Za-z0-9_]{20,})\"?", "Stripe Key"),
        (r"(?:sk|rk)_test_[A-Za-z0-9_]{20,}", "Stripe Key (standalone)"),
        (r"(?:sk|rk)_live_[A-Za-z0-9_]{20,}", "Stripe Live Key"),
    ],
    "slack": [
        (r"(?:slack[_-]?token|slack[_-]?bot|xox[bprs])", "Slack Token"),
        (r"xox[bprs]-[A-Za-z0-9_\\-]{10,}", "Slack Token (standalone)"),
    ],
    "jwt": [
        (r"eyJ[A-Za-z0-9_\\-]{20,}\\.[A-Za-z0-9_\\-]{20,}\\.[A-Za-z0-9_\\-]{20,}", "JWT Token"),
    ],
    "password": [
        (r"(?:password|passwd|pwd|pass)\s*[=:]\s*['\"]?([\\S]{6,})\"?", "Password (plaintext in file)"),
    ],
    "database": [
        (r"(?:mongodb|mysql|postgresql|postgres|redis|mssql|sqlite)://[A-Za-z0-9_:\\-./@]+", "Database Connection String"),
        (r"(?:DB[_-]?(?:URL|NAME|PASS|USER|HOST|PORT|PASSWD))\\s*[=:]", "Database Env Var (value hidden)"),
    ],
    "twilio": [
        (r"(?:twilio|sid|auth_token)\\s*[=:]", "Twilio Credential"),
    ],
    "telegram": [
        (r"(?:telegram[_-]?token|tg[_-]?token|bot[_-]?token)\\s*[=:]", "Telegram Bot Token"),
        (r"(?:\\d{6,}:[A-Za-z0-9_\\-]{20,})", "Telegram Bot Token (standalone)"),
    ],
}

# --- FILES TO SKIP ---
SKIP_EXTENSIONS = {
    '.pyc', '.pyo', '.class', '.jar', '.exe', '.dll', '.so', '.dylib',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.ico', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.wav',
    '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz', '.ttf', '.woff', '.woff2',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods',
    '.db', '.sqlite', '.sqlite3', '.mdb',
    '.bin', '.dat', '.dat', '.msgpack', '.pickle', '.pkl',
    '.arrow', '.parquet', '.feather', '.h5', '.hdf5',
    '.iso', '.dmg', '.vmdk', '.vhd', '.qcow2',
    '.min.js', '.min.css',  # minified — often contain hashed/bundled values
}
SKIP_BINARY = True
SKIP_PATHS = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env',
    '.tox', 'dist', 'build', '.eggs', '.mypy_cache', '.pytest_cache',
    'site-packages', '.next', '.nuxt', '.output', '.serverless',
    'target', 'bin', 'obj', 'coverage', '.gradle', '.m2',
}

# --- SAFE PATTERN FLAGS (what we DON'T flag) ---
SAFE_VALUES = {
    'your-api-key', 'your-api-key-here', 'your-secret-key', 'your-secret',
    'your-access-key', 'your-access-key-here', 'your-token', 'your-token-here',
    'insert-your-key', 'insert-your-secret', 'enter-your-key', 'enter-your-secret',
    'paste-your-key', 'paste-your-secret', 'put-your-key-here',
    'xxxxxxxx', 'xxxx-xxxx-xxxx', 'xxxxx', '******', '****', '---',
    'example', 'examples', 'example-key', 'example-secret',
    'placeholder', 'PLACEHOLDER', 'PLACEHOLDER_KEY',
    'changeme', 'changeme123', 'change-me', 'todo', 'fixme',
    'null', 'none', 'empty', 'string',
    'xxxxxxxxxxxx', 'xxx', 'key', 'secret', 'token', 'password',
}
SAFE_SUBSTRINGS = ['example', 'placeholder', 'your-', 'your_', 'insert-', 'insert_',
                   'paste-', 'paste_', 'enter-', 'enter_', 'changeme', 'change-me',
                   'replace-', 'replace_', 'add-', 'add_']
MIN_KEY_LENGTH = 8  # minimum length of actual matched key portion

# --- SCAN MODES ---
MODES = {
    "file": "Scan a single file",
    "dir": "Scan a directory (recursive)",
    "git": "Scan git history (past commits) for leaked secrets",
    "config": "Show/handle config file (create patterns.json)",
}

# --- RESULTS FORMAT ---
class ScanResult:
    def __init__(self, file_path, line_num, match_text, pattern_type, context_before="", context_after=""):
        self.file_path = file_path
        self.line_num = line_num
        self.match_text = match_text  # the captured key value (or match group)
        self.pattern_type = pattern_type
        self.context_before = context_before
        self.context_after = context_after

    def to_dict(self):
        return {
            "file": self.file_path,
            "line": self.line_num,
            "match": self.match_text[:100],  # truncate long keys in display
            "type": self.pattern_type,
        }

class ScanReport:
    def __init__(self, scan_path, timestamp=None):
        self.scan_path = scan_path
        self.timestamp = timestamp or datetime.now().isoformat()
        self.results = []
        self.stats = defaultdict(int)
        self.errors = []
        self.scan_duration_seconds = 0.0

    def add_result(self, result):
        self.results.append(result)
        self.stats[result.pattern_type] += 1

    def add_error(self, path, error):
        self.errors.append({"file": path, "error": str(error)})

    def summary(self):
        return {
            "scan_path": self.scan_path,
            "timestamp": self.timestamp,
            "total_findings": len(self.results),
            "unique_files_with_findings": len(set(r.file_path for r in self.results)),
            "findings_by_type": dict(self.stats),
            "errors": len(self.errors),
            "scan_duration_seconds": self.scan_duration_seconds,
        }

    def to_json(self):
        return json.dumps({
            "summary": self.summary(),
            "findings": [r.to_dict() for r in self.results],
            "errors": self.errors,
        }, indent=2, default=str)

    def to_text(self):
        lines = []
        lines.append("=" * 70)
        lines.append("HERMES API KEY & SECRET SCANNER")
        lines.append("=" * 70)
        lines.append(f"Scan Path  : {self.scan_path}")
        lines.append(f"Timestamp  : {self.timestamp}")
        lines.append(f"Findings   : {len(self.results)}")
        lines.append(f"Files Hit  : {len(set(r.file_path for r in self.results))}")
        if self.stats:
            lines.append(f"By Type    : {dict(self.stats)}")
        if self.errors:
            lines.append(f"Errors     : {len(self.errors)}")
        lines.append("=" * 70)
        lines.append("")

        if not self.results:
            lines.append("No secrets or API keys found!")
            return "\n".join(lines)

        # Group by file
        by_file = defaultdict(list)
        for r in self.results:
            by_file[r.file_path].append(r)

        for file_path, findings in sorted(by_file.items()):
            lines.append(f"--- {file_path} ({len(findings)} findings) ---")
            for f in findings:
                lines.append(f"  Line {f.line_num:>4} | [{f.pattern_type}]")
                # Show context lines around match
                if f.context_before:
                    lines.append(f"      Context: ...{f.context_before[-60:]}")
                lines.append(f"      Match    : {f.match_text[:80]}{'...' if len(f.match_text) > 80 else ''}")
                lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)

# --- SCANNER ---
class APIKeyFinder:
    def __init__(self, patterns_config=None, verbose=False):
        self.verbose = verbose
        self.patterns = self._load_patterns(patterns_config)
        self.compiled = self._compile_patterns()

    def _load_patterns(self, config_path):
        """Load patterns from config file or fallback to defaults."""
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                    return data.get("patterns", DEFAULT_PATTERNS)
            except Exception as e:
                print(f"[WARN] Could not load config {config_path}: {e}. Using defaults.")
        return DEFAULT_PATTERNS

    def _compile_patterns(self):
        """Compile all regex patterns into list of (compiled_regex, category, description)."""
        compiled = []
        for category, pat_list in self.patterns.items():
            for raw_pat, desc in pat_list:
                try:
                    compiled.append((re.compile(raw_pat, re.IGNORECASE), category, desc))
                except re.error as e:
                    if self.verbose:
                        print(f"[WARN] Bad regex '{raw_pat}': {e}")
        return compiled

    def _is_safe_value(self, value):
        """Check if matched value is a known safe placeholder."""
        v = value.strip().lower()
        if len(v) < MIN_KEY_LENGTH and not re.match(r'^[A-Za-z0-9_]+$', value):
            return True
        if v in SAFE_VALUES:
            return True
        return False

    def _is_safe_context(self, line_text):
        """Skip lines that are clearly just config documentation."""
        low = line_text.lower()
        safe_markers = ['# ', '//', '<!--', 'example', 'placeholder', 'your-', 'your_']
        # If line is mostly comment and contains only placeholder, skip
        stripped = line_text.strip()
        if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('<!--'):
            if any(s in low for s in ['example', 'placeholder', 'your-', 'your_', 'xxx', '...']):
                return True
        return False

    def _get_context(self, lines, idx, radius=1):
        """Get surrounding context lines."""
        start = max(0, idx - radius)
        end = min(len(lines), idx + radius + 1)
        return '\n'.join(lines[start:end])

    def scan_file(self, file_path, report):
        """Scan a single file for API keys/secrets."""
        if not os.path.isfile(file_path):
            report.add_error(file_path, "File not found")
            return

        ext = os.path.splitext(file_path)[1].lower()
        if ext in SKIP_EXTENSIONS:
            return  # skip binary/media/minified

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                lines = content.split('\n')
        except (IOError, PermissionError, UnicodeDecodeError) as e:
            # Binary file, permission issue — skip
            return

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip comment-only placeholder lines
            if self._is_safe_context(line):
                continue

            for regex, category, desc in self.compiled:
                for m in regex.finditer(line):
                    # Try to extract the captured group first; fallback to full match
                    matched_value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                    if not matched_value or len(matched_value) < MIN_KEY_LENGTH:
                        continue
                    if self._is_safe_value(matched_value):
                        continue

                    # Check if value is in a variable assignment context
                    context_before = ""
                    if i > 0:
                        context_before = lines[i - 1] if (i - 1) >= 0 else ""
                        context_before += "\n" + line

                    result = ScanResult(
                        file_path=str(file_path),
                        line_num=i + 1,
                        match_text=matched_value,
                        pattern_type=desc,
                        context_before=context_before,
                    )
                    report.add_result(result)

    def scan_directory(self, dir_path, recursive=True, extensions=None, report=None):
        """Scan directory recursively for files containing secrets."""
        if report is None:
            report = ScanReport(dir_path)

        dir_path = Path(dir_path)
        if not dir_path.exists():
            print(f"[ERROR] Path not found: {dir_path}")
            return report

        file_count = 0
        start = __import__('time').time()

        for root, dirs, files in os.walk(str(dir_path)):
            # Skip blacklisted directories
            dirs[:] = [d for d in dirs if d not in SKIP_PATHS]

            for fname in files:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()

                # Extension filter (optional)
                if extensions and ext not in extensions and ext not in {'.py', '.js', '.ts', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.env', '.conf', '.md', '.txt', '.xml', '.html', '.css', '.sh', '.bash', '.zsh', '.rc', '.properties', '.gradle', '.java', '.go', '.rs', '.rb', '.php', '.c', '.cpp', '.h', '.cs', '.swift', '.kt', '.scala', '.lua', '.r', '.R', '.sql', '.graphql', '.vue', '.svelte'}:
                    continue

                file_count += 1
                self.scan_file(fpath, report)

                if self.verbose and file_count % 500 == 0:
                    print(f"[SCAN] Processed {file_count} files...")

        report.scan_duration_seconds = round(__import__('time').time() - start, 2)
        return report

    def scan_git_history(self, repo_path, report=None):
        """Scan git history for past commits containing secrets."""
        import subprocess
        if report is None:
            report = ScanReport(repo_path)

        if not os.path.isdir(os.path.join(repo_path, '.git')):
            report.add_error(repo_path, "Not a git repo")
            return report

        try:
            # Get all commit hashes
            commits = subprocess.run(
                ['git', 'log', '--all', '--pretty=format:%H'],
                cwd=repo_path, capture_output=True, text=True, timeout=60
            ).stdout.strip().split('\n')

            if self.verbose:
                print(f"[GIT] Scanning {len(commits)} commits for secrets...")

            for commit in commits[:200]:  # limit to last 200 commits for safety
                try:
                    diff = subprocess.run(
                        ['git', 'show', commit],
                        cwd=repo_path, capture_output=True, text=True, timeout=30
                    ).stdout
                    if not diff:
                        continue

                    lines = diff.split('\n')
                    for i, line in enumerate(lines):
                        for regex, category, desc in self.compiled:
                            for m in regex.finditer(line):
                                matched_value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                                if matched_value and len(matched_value) >= MIN_KEY_LENGTH and not self._is_safe_value(matched_value):
                                    result = ScanResult(
                                        file_path=f"{repo_path}@{commit[:8]}",
                                        line_num=i + 1,
                                        match_text=matched_value,
                                        pattern_type=f"{desc} (git history)",
                                    )
                                    report.add_result(result)
                except subprocess.TimeoutExpired:
                    continue

        except Exception as e:
            report.add_error(repo_path, f"Git scan error: {e}")

        return report

# --- COMMAND LINE INTERFACE ---
def main():
    global MIN_KEY_LENGTH
    parser = argparse.ArgumentParser(
        description="Hermes API Key & Secret Finder — scan files/directories/git for leaked credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  hermes tools/api_key_finder.py /root/botmirror --recursive
  hermes tools/api_key_finder.py . --pattern sk- --json-out results.json
  hermes tools/api_key_finder.py . --git-history
  hermes tools/api_key_finder.py . --config-only   # create patterns template
        """
    )
    parser.add_argument("path", nargs="?", default=None, help="File or directory path to scan")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recursive scan (default for directories)")
    parser.add_argument("--git-history", action="store_true", help="Scan git commit history for past leaks")
    parser.add_argument("--config-only", action="store_true", help="Create a template patterns.json config")
    parser.add_argument("--pattern", "-p", help="Filter to find only keys containing this substring")
    parser.add_argument("--type", "-t", help="Filter by finding type (e.g., 'OpenAI API Key')")
    parser.add_argument("--json-out", "-j", help="Output results as JSON to file (stdout also)")
    parser.add_argument("--text-out", "-o", help="Output results as text to file")
    parser.add_argument("--min-length", "-m", type=int, default=MIN_KEY_LENGTH, help="Minimum matched key length")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Path to patterns JSON config")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--extensions-only", "-e", help="Only scan these extensions (comma-separated, e.g., '.py,.js,.env')")
    parser.add_argument("--exclude", "-x", help="Exclude paths matching this pattern")
    args = parser.parse_args()

    # Config-only mode
    if args.config_only:
        template = {
            "_comment": "API Key patterns config for Hermes API Key Finder",
            "_format": "Each entry: category -> list of (regex_pattern, description)",
            "patterns": DEFAULT_PATTERNS,
            "_customize": "Add your own patterns here following the same format.",
            "_example": {
                "my_service": [
                    [r"(?:my_service[_-]?key|my_secret)\\s*[=:]\\s*['\"]?([A-Za-z0-9_\\-]{16,})'", "My Service API Key"]
                ]
            }
        }
        config_path = args.config
        with open(config_path, 'w') as f:
            json.dump(template, f, indent=2)
        print(f"[OK] Config template written to {config_path}")
        print("[INFO] Edit this file to add custom patterns.")
        return

    # Determine scan mode
    target_path = args.path
    if args.path is None or not os.path.exists(target_path):
        if args.config_only:
            # already handled above
            pass
        else:
            print("[ERROR] Path required (or use --config-only to generate template). Usage: api_key_finder.py <path> [options]")
            sys.exit(1)
    if not os.path.exists(target_path):
        print(f"[ERROR] Path not found: {target_path}")
        sys.exit(1)

    # Initialize scanner
    scanner = APIKeyFinder(patterns_config=args.config if os.path.exists(args.config) else None,
                           verbose=args.verbose)

    # Override min length
    MIN_KEY_LENGTH = args.min_length

    report = ScanReport(target_path)

    if args.git_history:
        report = scanner.scan_git_history(target_path, report)
    elif os.path.isfile(target_path):
        scanner.scan_file(target_path, report)
    elif os.path.isdir(target_path):
        extensions = set(e.strip() for e in args.extensions_only.split(',')) if args.extensions_only else None
        scanner.scan_directory(target_path, recursive=args.recursive, extensions=extensions, report=report)

    # Apply filters
    if args.pattern:
        report.results = [r for r in report.results if args.pattern.lower() in r.match_text.lower()]
    if args.type:
        report.results = [r for r in report.results if args.type.lower() in r.pattern_type.lower()]

    # Output
    text_output = report.to_text()

    if args.json_out:
        with open(args.json_out, 'w') as f:
            f.write(report.to_json())
        print(f"[OK] JSON output written to {args.json_out}")

    if args.text_out:
        with open(args.text_out, 'w') as f:
            f.write(text_output)
        print(f"[OK] Text output written to {args.text_out}")

    if not args.json_out and not args.text_out:
        print(text_output)

    # Exit code: 1 if findings, 0 if clean
    sys.exit(1 if report.results else 0)


if __name__ == "__main__":
    main()
