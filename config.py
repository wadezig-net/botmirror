import os
from collections import deque
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_DIR = "/root/botmirror"
DOWNLOAD_DIR = f"{BASE_DIR}/downloads"
COOKIE_FILE = f"{BASE_DIR}/cookies.txt"
SFILE_SCRIPT = f"{BASE_DIR}/scripts/sfile_download.js"
SFILE_HTTP2_SCRIPT = f"{BASE_DIR}/scripts/sfile_http2.js"
FICHIER_SCRIPT = f"{BASE_DIR}/scripts/fichier_download.js"
TERABOX_SCRIPT = f"{BASE_DIR}/scripts/terabox_download.js"
DEVUPLOADS_SCRIPT = f"{BASE_DIR}/scripts/devuploads_download.js"
FICHIER_LOGIN_COOKIES = f"{BASE_DIR}/fichier_login_cookies.json"
FICHIER_PROXIES_FILE = os.getenv("FICHIER_PROXIES_FILE", f"{BASE_DIR}/proxies.txt")
NODE_BIN = "/usr/bin/node"

# ---- Proxy download (host yang diblokir / geo-locked) ----
# Dipakai di semua jalur download (requests, aria2, yt-dlp, browser node):
# devuploads, 1fichier, terabox, sfile, dll. Dua cara konfigurasi:
#   1. DOWNLOAD_PROXY = URL proxy tunggal, contoh:  http://user:pass@host:port
#      atau  socks5://host:port
#   2. DOWNLOAD_PROXIES_FILE = file daftar proxy (satu per baris) yang dirotasi
#      otomatis. Format tiap baris bisa:
#        http://user:pass@host:port
#        host:port:user:pass
#        user:pass@host:port
# Baris "#" diabaikan. Kalau dua-duanya kosong -> koneksi langsung.
DOWNLOAD_PROXY = os.getenv("DOWNLOAD_PROXY", "")
DOWNLOAD_PROXIES_FILE = os.getenv("DOWNLOAD_PROXIES_FILE", f"{BASE_DIR}/proxies.txt")

# ---- 2Captcha (captcha solving API v2) ----
# Dipakai saat gerbang shortlink (mis. sfl.gl) nampilin Cloudflare Turnstile
# / reCAPTCHA sebelum tujuan akhir dikeluarkan. Set CAPTCHA_API_KEY di .env.
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")
CAPTCHA_HOST = os.getenv("CAPTCHA_HOST", "https://api.2captcha.com")
CAPTCHA_POLL_INTERVAL = float(os.getenv("CAPTCHA_POLL_INTERVAL", "5"))
CAPTCHA_TIMEOUT = float(os.getenv("CAPTCHA_TIMEOUT", "120"))
# Sandbox mode (aktifkan di https://2captcha.com/setting#sandbox): semua task
# diselesaikan secara simulasi (gratis) -- bagus buat tes pipeline end-to-end
# SEBELUM nambah saldo. Turn OFF begitu mau dipakai buat produksi.
CAPTCHA_SANDBOX = os.getenv("CAPTCHA_SANDBOX", "").lower() in ("1", "true", "yes")

# ---- Debrid-Link (unlock host premium) ----
# Debrid-Link API v2 (https://debrid-link.com/api_doc): mengubah link host
# premium (Rapidgator, Uploaded, Turbobit, keep2share, dll) jadi direct link
# yang bisa di-download langsung. Butuh akun di debrid-link.com; API key bisa
# diambil di https://debrid-link.com/account/ bagian "API". Biarkan kosong
# kalau mau skip fitur ini.
DEBRID_LINK_API_KEY = os.getenv("DEBRID_LINK_API_KEY", "")
DEBRID_LINK_HOST = os.getenv("DEBRID_LINK_HOST", "https://debrid-link.com/api/v2")
DEBRID_LINK_TIMEOUT = float(os.getenv("DEBRID_LINK_TIMEOUT", "30"))
# Host premium (netloc, koma) yang dicegat langsung di jalur yt-dlp & di-unlock
# lewat Debrid-Link. 1Fichier sengaja nggak masuk daftar sini karena sudah ada
# resolver asli (fichier_headless) -- tapi tetap bisa lewat Debrid-Link sebagai
# jalur preferen ketika DEBRID_LINK_API_KEY di-set (lihat downloader/ytdlp.py).
DEBRID_LINK_DOMAINS = tuple(
    d.strip().lower()
    for d in os.getenv(
        "DEBRID_LINK_DOMAINS",
        "rapidgator.net,uploaded.net,turbobit.net,katfile.com,keep2share.cc,"
        "filecrypt.cc,crocko.com,hitfile.net,uploads.to,ulozto.cz,ulozto.net,"
        "uloz.net,bigfile.to,fileboom.me",
    ).split(",")
    if d.strip()
)

# ---- Real-Debrid (unlock host premium, alternatif Debrid-Link) ----
# Real-Debrid = layanan debrid berbayar paling populer; API-nya dipakai banyak
# tool (salah satunya userscript "Real-Debrid Premium Link Converter" yang
# ngubah link host jadi direct link via POST /unrestrict/link). Butuh akun di
# real-debrid.com; API token diambil di https://real-debrid.com/apitoken.
# Kosongkan kalau mau skip fitur ini. DICEKAT LEBIH DULU daripada Debrid-Link
# karena akun RD premium umumnya bisa unlock host yang debrid-link free tolak.
RD_API_KEY = os.getenv("RD_API_KEY", "")
RD_HOST = os.getenv("RD_HOST", "https://api.real-debrid.com/rest/1.0")
RD_TIMEOUT = float(os.getenv("RD_TIMEOUT", "60"))
# Host (netloc, koma) yang dicegat ke Real-Debrid sebelum jalur lain.
# 1Fichier sengaja tidak di sini: dia di-handle di branch khusus FICHIER
# (urutan: Real-Debrid -> Debrid-Link -> JDownloader -> headless asli).
RD_DOMAINS = tuple(
    d.strip().lower()
    for d in os.getenv(
        "RD_DOMAINS",
        "rapidgator.net,uploaded.net,ul.to,turbobit.net,katfile.com,"
        "keep2share.cc,hitfile.net,tezfiles.com,datafile.com,fboom.me,"
        "filefactory.com,userscloud.com,ddownload.com,hexupload.net,"
        "uploadraja.net,crocko.com,bigfile.to,ovh.to,uploadgig.com",
    ).split(",")
    if d.strip()
)

# ---- JDownloader via MyJDownloader (self-debrid style) ----
# JDownloader 2 menangani 100+ hoster (mediafire, mega, uptobox, rapidgator,
# uploaded, dll). Butuh JDownloader 2 yang JALAN DI MESIN YANG SAMA dengan bot
# (biar file hasilnya kebaca bot) dan akun my.jdownloader.org. Kosongkan kalau
# tidak dipakai.
JD_EMAIL = os.getenv("JD_EMAIL", "").strip()
JD_PASSWORD = os.getenv("JD_PASSWORD", "")
JD_DEVICE = os.getenv("JD_DEVICE", "JDownloader@botmirror").strip()
JD_TIMEOUT = float(os.getenv("JD_TIMEOUT", "1800"))
# Host (netloc, koma) yang dicegat ke JDownloader sebelum jalur yt-dlp.
# 1Fichier sengaja tidak di sini: dia di-handle di branch khusus (urutan:
# Debrid-Link -> JDownloader -> headless asli).
JD_DOMAINS = tuple(
    d.strip().lower()
    for d in os.getenv(
        "JD_DOMAINS",
        "mediafire.com,uptobox.com,mega.nz,mega.co.nz,rapidgator.net,uploaded.net,"
        "ul.to,turbobit.net,katfile.com,keep2share.cc,hitfile.net,tezfiles.com,"
        "datafile.com,fboom.me,filefactory.com,userscloud.com,gofile.io,"
        "ddownload.com,crocoblock.com,hexupload.net,uploadraja.net",
    ).split(",")
    if d.strip()
)

# ---- qBittorrent (torrent/magnet, self-debrid style) ----
# Butuh qBittorrent jalan di mesin yang sama dengan bot & web UI aktif.
qb_host = os.getenv("QB_HOST", "").strip()
QB_HOST = qb_host
QB_PORT = int(os.getenv("QB_PORT", "8080"))
QB_USER = os.getenv("QB_USER", "").strip()
QB_PASS = os.getenv("QB_PASS", "")
QB_TIMEOUT = float(os.getenv("QB_TIMEOUT", "3600"))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Owner ID dari env (dipisah dari .env bawa)
OWNER_ID = int(os.getenv("OWNER_ID", "440559453"))

# Telegram channel/group ID tujuan hasil mirror (-100...)
TELEGRAM_CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "-1004309539939"))

# Max task aktif per user (owner exemption)
MAX_TASKS_PER_USER = 2

# task yang lagi jalan (request_id -> ctx dict), dan riwayat singkat task yang
# udah selesai/gagal/dibatalin -- dipakai buat /status
task_registry = {}
pending_upload = {}
task_history = deque(maxlen=20)

app = Client(
    "mirrorbot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    # kalau kena FLOOD_WAIT singkat (di bawah threshold ini), Pyrogram otomatis
    # nunggu & retry sendiri alih-alih langsung raise exception dan gagal update
    sleep_threshold=30,
)

# Emoji animasi premium-style (custom emoji entity) untuk semua kiriman teks &
# caption, tanpa butuh akun Telegram Premium: pakai set resmi "AnimatedEmoji".
# Map emoji -> custom_emoji_id ada di emotes-premium/official_map.json; refresh
# dengan menjalankan scripts/fetch_official_emoji.py. Kalau map kosong/stabil
# gagal, semua pesan tetap jalan dengan emoji teks biasa.
try:
    from custom_emoji import install as _install_custom_emoji
    _install_custom_emoji()
except Exception as _emoji_err:  # jangan sampai bikin bot mati
    print(f"custom_emoji: skip ({_emoji_err})")


# ================== helper proxy download ==================
# Pool proxy cuma dipakai sebagai FALLBACK: setiap download coba koneksi
# LANGSUNG dulu, baru lewat proxy kalau jalur langsung gagal. Pool di-probe
# otomatis (deteksi skema http/socks5 + tes hidup ke devuploads.com:443) dan
# hasilnya disimpan di proxies_state.json. next_proxy() cuma ngeluarin proxy
# yang TERBUKTI hidup -- jadi nggak ada lagi download "ketahan 1%" gara-gara
# kena proxy mati/salah skema (temuan: sebagian besar baris yang dikasih user
# mati, yang hidup pun cuma ~5-11 KB/s, makanya dipakai fallback aja).

import base64 as _b64
import json as _json
import random as _random
import re as _re
import socket as _socket
import threading as _threading
import time as _time
from concurrent.futures import ThreadPoolExecutor as _TPE

PROXY_STATE_FILE = f"{BASE_DIR}/proxies_state.json"
PROXY_STATE_TTL = 900          # detik; proxy publik cepat mati, refresh tiap ~15 mnt
PROXY_REFRESH_INTERVAL = PROXY_STATE_TTL - 30
PROXY_PROBE_TARGET = ("devuploads.com", 443)
PROXY_WORKERS = 64

_pool_lock = _threading.Lock()
_pool = {}                     # "host:port" -> {"url": "...", "ts": epoch}


def _load_pool_state():
    try:
        if not os.path.isfile(PROXY_STATE_FILE):
            return {}
        with open(PROXY_STATE_FILE, encoding="utf-8") as _f:
            d = _json.load(_f)
        now = _time.time()
        out = {}
        if isinstance(d, dict):
            for k, v in d.items():
                url = v if isinstance(v, str) else v.get("url")
                if isinstance(url, str) and (
                    url.startswith("http://") or url.startswith("socks5://")
                ):
                    out[k] = {"url": url, "ts": now}
        return out
    except Exception:
        return {}


_pool.update(_load_pool_state())


def _raw_proxy_candidates():
    """Baris mentah pool dalam bentuk tanpa skema: 'host:port' / 'user:pass@host:port'."""
    cand = {}
    for path in (DOWNLOAD_PROXIES_FILE, FICHIER_PROXIES_FILE):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as _f:
                for line in _f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "://" in s:
                        rest = s.split("://", 1)[1]
                        if "@" in rest:
                            cred, hp = rest.rsplit("@", 1)
                            s = f"{cred}@{hp}"
                        else:
                            s = rest.split("@")[-1]  # buang userinfo aneh
                    try:
                        if "@" in s:
                            cred, hp = s.rsplit("@", 1)
                            cred = cred or ""
                        else:
                            cred, hp = "", s
                        h, p = hp.rsplit(":", 1)
                        p = int(p)
                        if not h or not _re.match(r"^[\w.\-]+$", h):
                            continue
                        if not (1 <= p <= 65535):
                            continue
                        key = f"{h}:{p}"
                        val = f"{cred}@{hp}" if cred else s
                        cand[key] = val
                    except Exception:
                        continue
        except OSError:
            continue
    return cand


def _tcp_probe(host, port, timeout=2.0):
    try:
        with _socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _classify(base):
    """Tes 1 proxy -> 'http://...' atau 'socks5://...' atau None (mati)."""
    if "@" in base:
        cred, hp = base.split("@", 1)
    else:
        cred, hp = "", base
    h, p = hp.rsplit(":", 1)
    p = int(p)
    if not _tcp_probe(h, p):
        return None
    th = PROXY_PROBE_TARGET
    sock = None
    try:
        sock = _socket.create_connection((h, p), timeout=6.0)
        sock.settimeout(6.0)
        if cred:
            sock.sendall(b"\x05\x01\x01\x02")
        else:
            sock.sendall(b"\x05\x01\x00")
        r = sock.recv(2)
        ok = None
        if len(r) == 2 and r[0] == 5 and r[1] == 0:
            ok = True
        elif len(r) == 2 and r[0] == 5 and r[1] == 2 and cred:
            u, pw = cred.split(":", 1)
            sock.sendall(b"\x01" + bytes([len(u)]) + u.encode()
                         + bytes([len(pw)]) + pw.encode())
            rr = sock.recv(2)
            ok = len(rr) == 2 and rr[1] == 0
        if ok:
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(th[0])])
                         + th[0].encode() + th[1].to_bytes(2, "big"))
            r = sock.recv(10)
            if len(r) >= 2 and r[1] == 0:
                return f"socks5://{cred}@{h}:{p}" if cred else f"socks5://{h}:{p}"
        sock.close()
    except Exception:
        pass
    try:
        if sock is None:
            sock = _socket.create_connection((h, p), timeout=6.0)
        sock.settimeout(6.0)
        req = f"CONNECT {th[0]}:{th[1]} HTTP/1.1\r\nHost: {th[0]}:{th[1]}\r\n"
        if cred:
            req += "Proxy-Authorization: Basic " \
                   + _b64.b64encode(cred.encode()).decode() + "\r\n"
        req += "\r\n"
        sock.sendall(req.encode())
        data = b""
        while len(data) < 128 and b"\r\n\r\n" not in data:
            chunk = sock.recv(256)
            if not chunk:
                break
            data += chunk
        if b" 200 " in data.split(b"\r\n", 1)[0]:
            return f"http://{cred}@{h}:{p}" if cred else f"http://{h}:{p}"
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def _probe_all(bases):
    urls = {}
    with _TPE(max_workers=PROXY_WORKERS) as ex:
        for hpk, url in zip(bases, ex.map(_classify, bases)):
            if url is not None:
                urls[hpk] = url
    return urls


def _refresh_pool():
    """Scan ulang proxy dari file yang belum teruji dalam TTL, update state."""
    candidates = _raw_proxy_candidates()
    now = _time.time()
    with _pool_lock:
        need = [c for hp, c in candidates.items()
                if hp not in _pool or now - _pool[hp]["ts"] >= PROXY_STATE_TTL]
    if not need:
        return
    res = _probe_all(list(set(need)))
    with _pool_lock:
        for hp, url in res.items():
            _pool[hp] = {"url": url, "ts": _time.time()}
        # buang yang udah nggak ada di list sumber ataupun udah lama banget
        for hp in list(_pool):
            if hp not in candidates:
                _pool.pop(hp, None)
    try:
        with open(PROXY_STATE_FILE, "w", encoding="utf-8") as _f:
            _json.dump({hp: v["url"] for hp, v in _pool.items()}, _f, indent=0)
    except OSError:
        pass


_proxy_refresher_started = [False]


def _start_pool_refresher():
    """Daemon thread: jaga pool tetap segar (proxy publik gampang mati)."""
    if _proxy_refresher_started[0]:
        return
    _proxy_refresher_started[0] = True

    def _runner():
        while True:
            _time.sleep(PROXY_REFRESH_INTERVAL)
            try:
                _refresh_pool()
            except Exception:
                pass

    _threading.Thread(target=_runner, daemon=True, name="proxy-pool-refresh").start()


_start_pool_refresher()


def next_proxy(force_socks=False):
    """Proxy URL acak dari pool proxy yang TERBUKTI hidup; '' kalau kosong.

    DOWNLOAD_PROXY (env) kalau diset selalu menang (proxy eksplisit user).
    force_socks=True buat jalur yang butuh socks (mis. bypass tertentu)."""
    with _pool_lock:
        if DOWNLOAD_PROXY:
            return DOWNLOAD_PROXY
        pool = _pool
        if not pool:
            return ""
        if force_socks:
            urls = [v["url"] for v in pool.values() if v["url"].startswith("socks5://")]
        else:
            urls = [v["url"] for v in pool.values()]
        return _random.choice(urls) if urls else ""


def requests_proxies():
    """Kamus proxies buat requests; None kalau nggak ada proxy hidup."""
    p = next_proxy()
    if not p:
        return None
    return {"http": p, "https": p}


def proxy_env():
    """Cuma kasih DOWNLOAD_PROXY kalau ada proxy eksplisit dari env.

    Subprocess node (devuploads/terabox/fichier/sfile) jalur direct-nya justru
    lebih cepat buat host yang bisa langsung, jadi pool nggak ikut diwariskan
    ke sini -- fallback pool diputuskan di tingkat download aja, bukan resolve."""
    env = {}
    if DOWNLOAD_PROXY:
        env["DOWNLOAD_PROXY"] = DOWNLOAD_PROXY
    if DOWNLOAD_PROXIES_FILE:
        env["DOWNLOAD_PROXIES_FILE"] = DOWNLOAD_PROXIES_FILE
    return env


def aria2_proxy_args():
    """Argumen aria2c buat memakai proxy (_all-proxy). Dipanggil pas RETRY aja."""
    p = next_proxy()
    return ["--all-proxy", p] if p else []


def ytdlp_proxy_args():
    """Argumen yt-dlp buat memakai proxy (`--proxy`). Dipanggil pas RETRY aja."""
    p = next_proxy()
    return ["--proxy", p] if p else []
