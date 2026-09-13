import re
import json
import os
import time
from datetime import date

from config import task_registry, OWNER_ID


USER_DB = "database/users.json"
QUOTA_DB = "database/quota.json"
BALANCE_DB = "database/balance.json"

# Folder database/ isi runtime (quota/users/balance) nggak di-track git.
# Di instalasi baru (VPS fresh) folder-nya belum ada -> pastikan dibuat +
# file default ikut di-seed biar nggak "No such file or directory".
for _db_path in (USER_DB, QUOTA_DB, BALANCE_DB):
    os.makedirs(os.path.dirname(_db_path), exist_ok=True)
    if not os.path.exists(_db_path):
        try:
            with open(_db_path, "w") as _f:
                json.dump({}, _f, indent=4)
        except OSError:
            pass

# Kalau users.json baru/dihapus datanya kosong, isi owner dari .env biar
# pemilik nggak ke-lock-out gara-gara daftar owner kosong.
try:
    with open(USER_DB) as _f:
        _users_db = json.load(_f)
    if isinstance(_users_db, dict) and not _users_db.get("owner"):
        _users_db["owner"] = [OWNER_ID]
        with open(USER_DB, "w") as _f:
            json.dump(_users_db, _f, indent=4)
except Exception:
    pass

# ---- Konfigurasi paywall mirror ----
FREE_QUOTA_PER_DAY = 2       # berapa file gratis per user per hari
FREE_MAX_FILE_GB = 2         # batas ukuran tiap file gratis (GB)
FREE_MAX_FILE_BYTES = FREE_MAX_FILE_GB * 1024 ** 3
# Rate saldo: berapa POIN yang dikenakan per 1 GB lewat kuota gratis.
PRICE_PER_GB = 1.0           # 1 poin = 1 GB mirror premium

# Kontak pemilik buat prompt topup/aktivasi (ganti sesuai kebutuhan).
OWNER_CONTACT = "@waaadezig"


def owner_prompt():
    return f"Hubungi owner {OWNER_CONTACT} buat topup saldo/aktivasi premium ya."


def friendly_error(exc):
    """Terjemahkan error teknis (HTTP status dll) jadi pesan yang bisa dimengerti user.

    Fokus utama: link download ber-signature (md5/expires) yang sudah expired dikembalikan
    server sebagai 410 Gone / 403 Forbidden / 404 -- sering disangka 'fitur gagal' padahal
    link-nya memang sudah mati di sisi server. Deteksi ini biar user tau harus minta link baru.
    """
    msg = str(exc)
    low = msg.lower()

    if "410" in low or "gone" in low:
        return (
            "⚠️ Link download-nya sudah KEDALUWARSA (server balikin 410 Gone).\n\n"
            "Link ber-signature kayak `?md5=...&expires=...` cuma valid beberapa waktu, "
            "terus mati total di server -- nggak bisa di-refresh oleh bot.\n\n"
            "Solusi: minta link BARU dari sumbernya (nggak usah pakai link lama itu), "
            "lalu /mirror link yang baru."
        )
    if ("403" in low or "forbidden" in low) and ("expired" in low or "md5" in low or "expires" in low or "sign" in low):
        return (
            "⚠️ Link download-nya expired/terblokir server (403).\n"
            "Minta link baru dari sumbernya, lalu /mirror lagi."
        )
    return msg


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


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


def is_owner(user_id):
    data = load_users()
    return user_id in data.get("owner", [])


def is_admin(user_id):
    data = load_users()

    return (
        user_id in data.get("owner", [])
        or user_id in data.get("admins", [])
    )


def is_premium(user_id):
    """Premium/admin/owner = dibebaskan dari kuota gratis (unlimited)."""
    data = load_users()

    return (
        user_id in data.get("owner", [])
        or user_id in data.get("admins", [])
        or user_id in data.get("premium", [])
    )


# ====================== KUOTA HARIAN GRATIS ======================

def _today():
    return date.today().isoformat()


def load_quota():
    return _load_json(QUOTA_DB, {})


def _quota_key(user_id):
    return str(user_id)


def get_daily_usage(user_id):
    """Return dict {count: int, bytes: int} utk user hari ini."""
    today = _today()
    data = load_quota()
    entry = data.get(_quota_key(user_id), {}).get(today, {})
    return {"count": int(entry.get("count", 0)), "bytes": int(entry.get("bytes", 0))}


def can_use_free(user_id, file_size_bytes):
    """Cek apakah mirror gratis masih bisa dipakai utk file ukuran tertentu.

    Owner/premium/admin bebas selalu (return True, None).
    Return (boleh: bool, alasan: str|None).
    """
    if is_premium(user_id):
        return True, None

    usage = get_daily_usage(user_id)
    if usage["count"] >= FREE_QUOTA_PER_DAY:
        return False, f"❌ Kuota gratis hari ini sudah habis ({FREE_QUOTA_PER_DAY} file)."
    if file_size_bytes and file_size_bytes > FREE_MAX_FILE_BYTES:
        return False, (
            f"❌ Ukuran file melebihi batas gratis ({FREE_MAX_FILE_GB} GB). "
            "Pakai saldo/topup premium buat file sebesar ini."
        )
    return True, None


def consume_free(user_id, file_size_bytes):
    """Catat pemakaian kuota gratis setelah mirror selesai."""
    if is_premium(user_id):
        return
    today = _today()
    data = load_quota()
    key = _quota_key(user_id)
    entry = data.setdefault(key, {})
    day = entry.setdefault(today, {"count": 0, "bytes": 0})
    day["count"] = int(day.get("count", 0)) + 1
    day["bytes"] = int(day.get("bytes", 0)) + int(file_size_bytes or 0)
    _save_json(QUOTA_DB, data)


# ====================== SALDO INTERNAL (POIN) ======================

def load_balance():
    return _load_json(BALANCE_DB, {})


def get_balance(user_id):
    return float(load_balance().get(str(user_id), 0.0))


def add_balance(user_id, points):
    data = load_balance()
    key = str(user_id)
    data[key] = float(data.get(key, 0.0)) + float(points)
    _save_json(BALANCE_DB, data)
    return data[key]


def deduct_balance(user_id, points):
    """Kurangi saldo. Return (sukses: bool, sisa: float)."""
    data = load_balance()
    key = str(user_id)
    cur = float(data.get(key, 0.0))
    if cur < float(points):
        return False, cur
    data[key] = cur - float(points)
    _save_json(BALANCE_DB, data)
    return True, data[key]


def cost_in_points(file_size_bytes):
    """Biaya (poin) utk mirror file premium sebesar size tertentu."""
    return max(PRICE_PER_GB, round((file_size_bytes or 0) / 1024 ** 3 * PRICE_PER_GB, 2))


def can_pay_with_balance(user_id, file_size_bytes):
    """Cek apakah user punya saldo cukup utk bayar file premium."""
    return get_balance(user_id) >= cost_in_points(file_size_bytes)


def charge_for_mirror(user_id, file_size_bytes):
    """Selesaikan penentuan biaya utk satu mirror SAAT file size sudah pasti.

    - owner/premium/admin -> gratis, tidak displace kuota.
    - user biasa:
        * size <= FREE_MAX & masih ada kuota gratis -> GRATIS (consume kuota).
        * selain itu -> potong SALDO. Kalau saldo kurang -> ditolak.

    Return (boleh: bool, dibayar_dengan_saldo: bool, pesan: str|None).
    """
    if is_premium(user_id):
        return True, False, None

    size = int(file_size_bytes or 0)

    # file kecil & masih ada kuota gratis -> gratis
    if size <= FREE_MAX_FILE_BYTES:
        ok, reason = can_use_free(user_id, size)
        if ok:
            consume_free(user_id, size)
            return True, False, None

    # lewat kuota / file besar -> pakai saldo
    cost = cost_in_points(size)
    success, _ = deduct_balance(user_id, cost)
    if not success:
        return (
            False,
            False,
            f"❌ Saldo nggak cukup buat mirror file ini.\n\n"
            f"Kebutuhan: {cost:.2f} 💎\nSaldo kamu: {get_balance(user_id):.2f} 💎\n\n"
            f"Hari ini kamu udah pakai "
            f"{get_daily_usage(user_id)['count']}/{FREE_QUOTA_PER_DAY} mirror gratis.\n"
            f"Topup saldo 20k/bulan biar bisa akses premium mirror lagi. "
            f"Gunakan /topup dan hub @waaadezig untuk aktivasi premium.\n"
            f"❤️happy mirror❤️",
        )
    return True, True, None


def probe_url_size(url, timeout=15):
    """Best-effort deteksi ukuran file dari URL (HEAD -> content-length).

    Return None kalau tidak bisa ditebak (mis. situs JS/stream/redirect complex).
    Ini hanya untuk filter awal; keputusan final tetap pakai ukuran file asli
    setelah download.
    """
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        cl = r.headers.get("content-length")
        if cl and cl.isdigit():
            return int(cl)
    except Exception:
        pass
    return None

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


def is_owner_or_premium(user_id):
    """
    Khusus buat privasi tampilan /status -- owner & premium task-nya
    disamarkan jadi 'Private Task', beda dari is_premium() yang lagi
    di-nonaktifkan sementara (return True buat semua user).
    """
    data = load_users()
    return (
        user_id in data.get("owner", [])
        or user_id in data.get("premium", [])
    )


def free_quota_status_line(user_id):
    """Info sisa kuota gratis buat panel status. None untuk owner/premium/admin."""
    if is_premium(user_id):
        return None
    usage = get_daily_usage(user_id)
    remaining = FREE_QUOTA_PER_DAY - usage["count"]
    if remaining > 0:
        return (
            f"🎁 Sisa kuota gratis hari ini: {remaining}x mirror "
            f"(maks {FREE_MAX_FILE_GB} GB/file)"
        )
    return (
        f"🧾 Kuota gratis hari ini habis — segera topup saldo buat mirror lagi. {owner_prompt()}"
    )


def get_active_task_count(user_id):
    """Hitung task aktif milik user tertentu di task_registry."""
    return sum(
        1 for ctx in task_registry.values()
        if ctx.get("user_id") == user_id
    )


def can_start_task(user_id):
    """Return (boleh: bool, pesan: str). Owner dikecualikan dari limit."""
    from config import MAX_TASKS_PER_USER
    if is_admin(user_id):
        return True, ""
    if get_active_task_count(user_id) >= MAX_TASKS_PER_USER:
        return False, (
            f"❌ Limit task kamu sudah capai ({MAX_TASKS_PER_USER} aktif). "
            "Tunggu salah satu selesai/cancel dulu."
        )
    return True, ""
