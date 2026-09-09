import os
import re
import json
import time
import asyncio
import shutil
import http.cookiejar

from config import BASE_DIR
from status_ui import render_status
from utils import clean_filename

# Domain-domain kaceku yang didukung script ini
KACEKU_DOMAINS = ("kaceku.onrender.com", "drive.kaceku.workers.dev")

# File cookie hasil export dari browser (SETELAH login cepat via @KacekuBot).
# Disimpan di luar git. Format Netscape (seperti yang didukung curl/gdown).
KACEKU_COOKIE_FILE = os.path.join(BASE_DIR, "kaceku_cookies.txt")

_KACEKU_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_ID_RE = re.compile(r"id=([A-Za-z0-9_-]{15,})")
_FIND_ID_RE = re.compile(r"/f/([A-Za-z0-9_-]{15,})")
_NAME_RE = re.compile(r'class="tth"> Name</span></th>\s*<td>(.*?)</td>', re.S)
_IS_FOLDER_RE = re.compile(r'application/vnd\.google-apps\.folder')


def _load_cookie_jar():
    """Muati cookie kaceku dari file Netscape. Return requests session berisi cookie."""
    import requests

    sess = requests.Session()
    sess.headers.update({"User-Agent": _KACEKU_UA})
    from config import requests_proxies
    sess.proxies = requests_proxies() or {}
    if not os.path.isfile(KACEKU_COOKIE_FILE):
        raise Exception(
            f"File cookie kaceku belum ada: {KACEKU_COOKIE_FILE}\n"
            "Cara dapatkan: login cepat via @KacekuBot di browser ke\n"
            "  https://drive.kaceku.workers.dev/0:findpath?id=<ID>\n"
            "  atau https://kaceku.onrender.com/f/<ID>\nlalu export cookie "
            "(format Netscape) ke file tersebut."
        )
    jar = http.cookiejar.MozillaCookieJar(KACEKU_COOKIE_FILE)
    try:
        jar.load()
    except Exception as e:
        raise Exception(
            f"Gagal membaca cookie kaceku {KACEKU_COOKIE_FILE}: {e}\n"
            "Pastikan format file berjudul '# Netscape HTTP Cookie File'."
        ) from e
    sess.cookies.update(jar)
    return sess


def _parse_gdrive_id(url):
    m = _FIND_ID_RE.search(url) or _ID_RE.search(url)
    return m.group(1) if m else None


def _is_worker_index(url):
    return "workers.dev" in url and ("/0:" in url or "findpath" in url)


async def _fetch(sess, url, ctx, is_json=False):
    await render_status(ctx, f"🔎 Membaca {url.split('/')[2]}…")

    def _get():
        r = sess.get(url, timeout=40, allow_redirects=True)
        return r

    r = await asyncio.to_thread(_get)
    if r.status_code in (401, 403) or "Sign in" in r.text:
        raise Exception(
            f"Unauthorized (HTTP {r.status_code}) — cookie kaceku tidak valid/expired. "
            "Login ulang via @KacekuBot lalu export cookie baru ke "
            f"{KACEKU_COOKIE_FILE}."
        )
    r.raise_for_status()
    return r.json() if is_json else r.text


async def resolve_kaceku(url, ctx):
    """Resolve halaman kaceku (render or worker) -> (file_name, is_folder, gdrive_id)."""
    sess = _load_cookie_jar()
    gid = _parse_gdrive_id(url)

    if _is_worker_index(url):
        # worker index: id query lang mengandung gdrive id target (bukan nama).
        return None, False, gid

    html = await _fetch(sess, url, ctx)
    m = _NAME_RE.search(html)
    name = clean_filename(m.group(1)) if m else None
    is_folder = bool(_IS_FOLDER_RE.search(html))
    return name, is_folder, gid


async def kaceku_download(url, work_dir, ctx):
    """Entry point: download file/folder dari link kaceku (render atau index worker)."""
    return await _handle(url, work_dir, ctx)


async def _handle(url, work_dir, ctx):
    sess = _load_cookie_jar()
    gid = _parse_gdrive_id(url)
    if not gid:
        raise Exception("Gagal mengekstrak ID dari link kaceku.")

    if _is_worker_index(url):
        return await _download_from_worker(sess, url, gid, work_dir, ctx)
    return await _download_from_render(sess, url, gid, work_dir, ctx)


async def _download_from_render(sess, url, gid, work_dir, ctx):
    html = await _fetch(sess, url, ctx)
    m = _NAME_RE.search(html)
    name = clean_filename(m.group(1)) if m else "file"
    is_folder = bool(_IS_FOLDER_RE.search(html))

    if is_folder:
        return await _download_from_gdrive_folder(gid, name, sess, work_dir, ctx)

    # file langsung via gdrive Download
    return await _download_from_gdrive_file(gid, name, sess, work_dir, ctx)


async def _download_from_worker(sess, url, gid, work_dir, ctx):
    """Worker index sudah menyediakan akses file via endpoint /0:<path>?view=false"""
    # Endpoint dasar: ganti query jadi view=false (download)
    dl_url = url
    if "view=false" not in dl_url:
        dl_url = dl_url + ("&" if "?" in dl_url else "?") + "view=false"

    # Header: pastikan respons dianggap download oleh worker
    await render_status(ctx, "📥 Mendownload dari index worker…")

    def _dl():
        r = sess.get(dl_url, timeout=60, stream=True, allow_redirects=True)
        return r

    r = await asyncio.to_thread(_dl)
    if r.status_code in (401, 403) or "Sign in" in r.text[:2000]:
        raise Exception(
            f"Unauthorized (HTTP {r.status_code}) pada index. "
            "Cookie kaceku invalid. Login ulang & export cookie baru."
        )
    r.raise_for_status()

    # coba ambil nama dari content-disposition, fallback ke id
    cd = r.headers.get("content-disposition", "")
    fname = re.search(r'filename="?([^";]+)"?', cd)
    fname = clean_filename(fname.group(1)) if fname else f"{gid}.bin"

    filepath = os.path.join(work_dir, fname)
    total = int(r.headers.get("content-length") or 0)
    downloaded = 0
    start = time.monotonic()
    last_update = [0.0]
    loop = asyncio.get_running_loop()

    with open(filepath, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_update[0] < 2.5 and downloaded < total:
                continue
            last_update[0] = now
            pct = (downloaded / total * 100) if total else 0
            asyncio.run_coroutine_threadsafe(
                render_status(
                    ctx, "📥 Mendownload", percent=pct,
                    processed=downloaded, total=total,
                ),
                loop,
            )
    return filepath


async def _download_from_gdrive_file(gid, name, sess, work_dir, ctx):
    """Unduh file publik via Google Drive uc endpoint (dengan cookie bantu)."""
    import requests

    await render_status(ctx, "📥 Mendownload file dari Google Drive…")

    def _get_download_page():
        r = sess.get(
            f"https://drive.google.com/uc?id={gid}&export=download",
            timeout=40, allow_redirects=True,
        )
        return r

    r = await asyncio.to_thread(_get_download_page)
    if "Sign-in" in r.text or r.status_code in (401, 403):
        raise Exception(
            "File/folder Google Drive butuh akses (Sign-in). "
            "Pastikan file di-share 'Anyone with the link' atau pilih "
            "metode kedua (via index worker)."
        )

    # deteksi uc confirm token
    confirm = re.search(r'name="confirm" value="([0-9A-Za-z_-]+)"', r.text)
    final_url = r.url
    if confirm:
        final_url = (
            f"https://drive.usercontent.google.com/download?"
            f"id={gid}&confirm={confirm.group(1)}&export=download"
        )
    elif "Dada" not in r.text and "text/html" in r.headers.get("content-type", ""):
        # possible direct file -> use final r.url
        pass

    # filename dari header CD
    cd = r.headers.get("content-disposition", "")
    fm = re.search(r'filename="?([^";]+)"?', cd)
    fname = clean_filename(fm.group(1)) if fm else clean_filename(f"{name or gid}")
    filepath = os.path.join(work_dir, fname)
    total = int(r.headers.get("content-length") or 0)
    downloaded = 0
    start = time.monotonic()
    last_update = [0.0]
    loop = asyncio.get_running_loop()

    def _stream():
        r2 = sess.get(final_url, timeout=60, stream=True, allow_redirects=True)
        with open(filepath, "wb") as f:
            for chunk in r2.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
                    yield len(chunk)

    with open(filepath, "wb") as f:
        r2 = await asyncio.to_thread(
            lambda: sess.get(final_url, timeout=60, stream=True, allow_redirects=True)
        )
        for chunk in r2.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_update[0] < 2.5 and downloaded < total:
                continue
            last_update[0] = now
            pct = (downloaded / total * 100) if total else 0
            asyncio.run_coroutine_threadsafe(
                render_status(
                    ctx, "📥 Mendownload", percent=pct,
                    processed=downloaded, total=total,
                ),
                loop,
            )
    return filepath


async def _download_from_gdrive_folder(gid, name, sess, work_dir, ctx):
    """Folder via Google Drive — butuh akses login. Tanpa itu, gagal jelas."""
    raise Exception(
        "Folder Google Drive kaceku butuh akses login (Sign-in). "
        "File dalam folder tidak bisa di-list tanpa akun Google berizin.\n"
        "Opsi: (1) gunakan link index worker yang mengarah langsung ke file, "
        "atau (2) share file secara publik ('Anyone with the link')."
    )
