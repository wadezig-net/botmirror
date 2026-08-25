import re
import json
import os


USER_DB = "database/users.json"


def load_users():
    if not os.path.exists(USER_DB):
        return {
            "owner": [],
            "admins": [],
            "premium": []
        }

    with open(USER_DB, "r") as f:
        return json.load(f)


def save_users(data):
    with open(USER_DB, "w") as f:
        json.dump(data, f, indent=4)


def is_admin(user_id):
    data = load_users()

    return (
        user_id in data["owner"]
        or user_id in data["admins"]
    )


def is_premium(user_id):
    return True  # <-- SEMENTARA: nonaktifkan whitelist, semua user diizinkan
    data = load_users()

    return (
        user_id in data["owner"]
        or user_id in data["admins"]
        or user_id in data["premium"]
    )

def get_progress_bar(current, total=None):
    if total is not None:
        if total == 0:
            percent = 0
        else:
            percent = (current / total) * 100
    else:
        percent = current

    length = 10

    filled = int(length * percent / 100)

    bar = "█" * filled + "░" * (length - filled)

    return f"[{bar}] {percent:.1f}%"



def format_bytes(size):

    if size == 0:
        return "0 B"

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB"
    ]

    index = 0

    while size >= 1024 and index < len(units)-1:
        size /= 1024
        index += 1

    return f"{size:.2f} {units[index]}"



def format_duration(seconds):

    seconds = int(seconds)

    h = seconds // 3600
    m = (seconds % 3600)//60
    s = seconds % 60

    if h:
        return f"{h}h {m}m {s}s"

    if m:
        return f"{m}m {s}s"

    return f"{s}s"

def format_speed(speed):

    if speed is None:
        return "0 B/s"

    if speed == 0:
        return "0 B/s"

    units = [
        "B/s",
        "KB/s",
        "MB/s",
        "GB/s"
    ]

    index = 0

    while speed >= 1024 and index < len(units)-1:
        speed /= 1024
        index += 1

    return f"{speed:.2f} {units[index]}"

def clean_filename(filename):
    """
    Membersihkan nama file agar aman dipakai di filesystem.
    """
    if not filename:
        return "downloaded_file.bin"

    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    filename = filename.strip()

    return filename or "downloaded_file.bin"
