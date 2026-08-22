import re


def clean_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name[:100]


def get_progress_bar(percent, total_blocks=10):
    try:
        filled = int(float(percent) / 100 * total_blocks)
    except (ValueError, TypeError):
        filled = 0
    filled = max(0, min(total_blocks, filled))
    return "■" * filled + "□" * (total_blocks - filled)


def format_bytes(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f}{unit}" if unit != "B" else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.2f}PB"


def format_speed(bytes_per_sec):
    return format_bytes(bytes_per_sec) + "/s"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"
