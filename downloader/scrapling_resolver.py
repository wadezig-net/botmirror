"""Resolver link download berbasis Scrapling — pengganti/pelengkap script Node headless.

Fitur edukasi: memakai Scrapling (StealthyFetcher/DynamicFetcher) untuk membuka
halaman share host, menangkap XHR/fetch yang relevan (capture_xhr), mengekstrak
link file asli + cookie session, lalu menyerahkan hasil dalam format yang sama
dengan run_node_link_finder() dari browser_link_capture.py -- jadi drop-in
untuk download_resolved_link() / download_resolved_link_aria2() yang sudah ada.
"""
import os
import re
import json
import time
import asyncio
import tempfile

from status_ui import render_status
from utils import clean_filename

# disimpan di luar git
SCRAPLING_COOKIE_DIR = os.path.join("/tmp", "scrapling_cookies")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


async def _import_fetch(stealth=True):
    """Import fetcher sesuai kebutuhan (lazy, biar modal parser tetap bisa dipakai
    tanpa dependensi fetcher terpasang)."""
    if stealth:
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher
    from scrapling.fetchers import DynamicFetcher
    return DynamicFetcher


def _is_direct_response(r):
    """Cek apakah respons headers menandakan FILE BINARY (bukan halaman/API JSON).

    Anggap direct hanya jika ada content-disposition attachment/filename,
    atau content-type benar-benar binary/octet-stream. JSON/API/metadata
    TIDAK dianggap file langsung (bisa jadi berisi dlink di body-nya).
    """
    headers = getattr(r, "headers", {}) or {}
    ct = (headers.get("content-type") or "").lower()
    cd = headers.get("content-disposition", "") or ""

    if '"' in cd or "attachment" in cd.lower() or "filename" in cd.lower():
        return True
    if "text/html" in ct:
        return False
    if "application/json" in ct or "text/json" in ct or "+json" in ct:
        return False
    return "octet-stream" in ct or "application/zip" in ct or "binary" in ct


def _fname_from_headers(headers, fallback="downloaded_file.bin"):
    cd = headers.get("content-disposition", "") if headers else ""
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return clean_filename(m.group(1))
    return clean_filename(fallback)


def _looks_like_download_link(url):
    """Heuristik link yang kemungkinan besar binary/file."""
    if not url:
        return False
    path = url.split("?")[0].lower()
    ext = os.path.splitext(path)[1].lstrip(".")
    if ext in (
        "zip", "rar", "7z", "tar", "gz",
        "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "ts",
        "mp3", "m4a", "wav", "flac", "aac", "ogg",
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
        "jpg", "jpeg", "png", "gif", "webp", "svg",
        "apk", "exe", "iso",
    ):
        return True
    return "download" in url or "dlink" in url or "file" in url


async def _extract_direct_from_xhr(captured_xhr):
    """Dari daftar captured XHR, cari endpoint yang menyerahkan file.

    capture_xhr adalah list Response object dengan atribut .url, .status,
    .headers, .body. Prioritas:
      1. respons JSON yang berisi key dlink/link/download_url/file/src
      2. respons binary/octet-stream langsung (dengan content-disposition)
    """
    if not captured_xhr:
        return None, None, None

    # PASS 1: cari link di body JSON dulu (biasanya API /share/list, /api/v2/...)
    for resp in captured_xhr:
        if resp.status != 200:
            continue
        url = getattr(resp, "url", "")
        headers = getattr(resp, "headers", {}) or {}

        body = getattr(resp, "body", None)
        text = ""
        if body is not None:
            try:
                text = body.decode("utf-8", errors="ignore") if isinstance(body, (bytes, bytearray)) else str(body)
            except Exception:
                text = ""
        if not text:
            text = getattr(resp, "text", "")

        if text.lstrip().startswith("{"):
            try:
                data = json.loads(text)
            except Exception:
                data = None
            if data:
                dlink = _json_find_dlink(data)
                if dlink:
                    return dlink, os.path.basename(dlink.split("?")[0]) or "file", headers

    # PASS 2: respons binary langsung
    for resp in captured_xhr:
        if resp.status != 200:
            continue
        url = getattr(resp, "url", "")
        headers = getattr(resp, "headers", {}) or {}
        if _is_direct_response(resp):
            fname = _fname_from_headers(headers, os.path.basename(url.split("?")[0]) or "file")
            return url, fname, headers

    return None, None, None


def _json_find_dlink(data, depth=0):
    """Cari nilai string http(s) pada key yang dikenal di dict JSON (rekursif)."""
    if depth > 5 or data is None:
        return None
    if isinstance(data, dict):
        # prioritas key spesifik
        for key in ("dlink", "download_link", "download_url", "direct_link", "direct_url", "file_url", "link", "url"):
            val = data.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
        for val in data.values():
            found = _json_find_dlink(val, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for val in data:
            found = _json_find_dlink(val, depth + 1)
            if found:
                return found
    return None


async def _extract_direct_from_dom(page):
    """Cari link file langsung di DOM (tag <a download>, <video>, <audio>, <source>)."""
    candidates = []
    try:
        for href in page.css("a::attr(href)").getall():
            if href and _looks_like_download_link(href):
                candidates.append(href)
        for src in page.css("video source::attr(src), audio source::attr(src), video::attr(src), audio::attr(src)").getall():
            if src and src.startswith("http"):
                candidates.append(src)
    except Exception:
        return None

    if not candidates:
        return None
    # pilih yang paling "probable" (punya ekstensi file)
    for c in candidates:
        if _looks_like_download_link(c):
            return c
    return candidates[0]


async def _resolve_relative(url, base):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    from urllib.parse import urljoin
    return urljoin(base, url)


async def _save_cookies_json(cookies, work_dir):
    """Simpan cookie (tuple Response cookie) jadi file JSON dipakai download_resolved_link."""
    if not cookies:
        return None
    os.makedirs(SCRAPLING_COOKIE_DIR, exist_ok=True)
    cookie_path = os.path.join(SCRAPLING_COOKIE_DIR, f"cookies_{int(time.time() * 1000)}.json")
    data = []
    for c in cookies:
        try:
            data.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
            })
        except AttributeError:
            continue
    if not data:
        return None
    with open(cookie_path, "w") as f:
        json.dump(data, f)
    return cookie_path


async def scrapling_find_links(url, work_dir, ctx, stealth=True, network_idle=True,
                               capture_pattern=r".*(download|dlink|file|stream|api).*",
                               timeout=120, page_action=None, max_xhr_scan=12):
    """Resolver generik: buka halaman dengan Scrapling, temukan direct link.

    Mirip run_node_link_finder() — mengembalikan dict:
      {ok, direct_url, filename, referer, cookie_file}
    kompatibel dengan download_resolved_link() / download_resolved_link_aria2().
    """
    await render_status(ctx, "🕸️ Membuka halaman (Scrapling)…")
    Fetcher = await _import_fetch(stealth)
    start = time.monotonic()
    args = dict(
        headless=True,
        network_idle=network_idle,
        # capture_xhr menerima REGEX (bukan glob). Jangan kirim "*" polos.
        capture_xhr=capture_pattern,
    )
    if page_action is not None:
        args["page_action"] = page_action

    try:
        page = await asyncio.wait_for(
            Fetcher.async_fetch(url, **args),
            timeout=timeout,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"Scrapling gagal membuka halaman: {e}",
        }

    await render_status(ctx, "🔎 Memindai link download (Scrapling)…")

    # 1) cari dari captured XHR
    captured = list(getattr(page, "captured_xhr", None) or [])[:max_xhr_scan]
    direct_url, fname, headers = await _extract_direct_from_xhr(captured)

    # 2) fallback: scan DOM
    if not direct_url:
        hit = await _extract_direct_from_dom(page)
        if hit:
            direct_url = await _resolve_relative(hit, url)
            fname = os.path.basename(direct_url.split("?")[0]) or "downloaded_file.bin"
            headers = None

    if not direct_url:
        return {
            "ok": False,
            "error": (
                f"Scrapling tidak menemukan link download setelah "
                f"{int(time.monotonic() - start)}s (XHR: {len(captured)}, "
                "tidak ada endpoint file/JSON dlink). Coba target lain."
            ),
        }

    cookie_file = await _save_cookies_json(getattr(page, "cookies", ()) or (), work_dir)
    return {
        "ok": True,
        "direct_url": direct_url,
        "filename": fname,
        "referer": url,
        "cookie_file": cookie_file,
    }


async def scrapling_try(url, work_dir, ctx, timeout=120, multi_connection=False):
    """Helper end-to-end: resolve + download pakai Scrapling.

    Cocok dipakai sebagai fallback satu panggilan dari download_via_url.
    Kembalikan path file yang di-download, atau raise Exception jelas.
    """
    from downloader.browser_link_capture import download_resolved_link, download_resolved_link_aria2

    result = await scrapling_find_links(url, work_dir, ctx, timeout=timeout)
    if not result.get("ok"):
        raise Exception(result.get("error", "Gagal resolve pakai Scrapling."))

    if multi_connection:
        path = await download_resolved_link_aria2(result, work_dir, ctx)
        if path:
            return path
        # fallback single koneksi kalau server tolak Range
    return await download_resolved_link(result, work_dir, ctx)
