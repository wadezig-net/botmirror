import os
import re
import time
import asyncio
import requests

from utils import clean_filename
from status_ui import render_status
from config import requests_proxies

# file di atas threshold ini & server support Range -> otomatis pakai
# multi-connection (aria2c). File kecil nggak worth overhead-nya.
ARIA2_MIN_BYTES = 100 * 1024 * 1024  # 100 MB
ARIA2_CONNECTIONS = 8


async def _aria2_multi_download(url, work_dir, ctx, headers=None, total=None):
    """Download pakai aria2c multi-koneksi paralel (buat server yang nge-throttle
    bandwidth per-koneksi tapi support Range). Paneli progress tetap update.
    Direct-first; retry sekali lewat proxy live kalau jalur langsung gagal."""
    import shutil

    aria2 = shutil.which("aria2c")
    if not aria2:
        return None  # caller fallback ke single-connection

    fname = clean_filename(url.split("/")[-1].split("?")[0]) or "file.bin"
    filepath = os.path.join(work_dir, fname)

    async def run_once(proxy_url=None):
        cmd = [
            aria2,
            "--no-conf",
            "--summary-interval", "2",
            "--console-log-level=notice",
            "-x", str(ARIA2_CONNECTIONS),
            "-s", str(ARIA2_CONNECTIONS),
            "-k", "1M",
            "--dir", work_dir,
            "--out", fname,
            "--file-allocation=none",
            "--allow-overwrite=true",
            "--auto-file-renaming=false",
            "--max-tries", "5",
            "--retry-wait", "3",
            "--timeout", "30",
            "--connect-timeout", "15",
            "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        for k, v in (headers or {}).items():
            cmd += ["--header", f"{k}: {v}"]
        if proxy_url:
            cmd += ["--all-proxy", proxy_url]
        cmd.append(url)

        await render_status(ctx, f"🔗 Download {ARIA2_CONNECTIONS} koneksi paralel (aria2c)")

        # buang env node-IPC (sama seperti di ytdlp.py) biar subprocess ga salah sangka
        clean_env = os.environ.copy()
        for var in ("NODE_CHANNEL_FD", "NODE_UNIQUE_ID", "NODE_CHANNEL_SERIALIZATION_MODE"):
            clean_env.pop(var, None)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=clean_env,
        )
        ctx["process"] = process

        # lazy import biar ga bikin siklus & hanya kalau perlu
        from downloader.browser_link_capture import _aria2_parse_line

        dl_last_update = [0.0]
        total_bytes = total or 0

        try:
            async for raw_line in process.stdout:
                line = raw_line.decode(errors="ignore")
                print(line, end="")
                parsed = await _aria2_parse_line(line)
                if not parsed:
                    continue
                percent, processed, speed = parsed
                now = time.monotonic()
                if now - dl_last_update[0] < 2.5 and percent < 100:
                    continue
                dl_last_update[0] = now
                await render_status(
                    ctx, "Download", percent=percent,
                    processed=processed, total=total_bytes, speed=speed,
                )
            await process.wait()
        finally:
            ctx["process"] = None

        if process.returncode != 0 or not os.path.isfile(filepath):
            return None
        return filepath

    result = await run_once()
    if result:
        return result

    # direct gagal -> retry sekali pakai proxy live (pool yang ketemu tadi)
    prox = requests_proxies()
    if prox:
        p_url = prox["https"] or prox["http"]
        await render_status(ctx, f"🔄 Coba via {p_url.split('@')[-1][:40]}...")
        for leftover in (filepath, filepath + ".aria2"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        result = await run_once(p_url)
        if result:
            return result
    return None


async def generic_http_download(url, work_dir, ctx, headers=None):
    """
    Fallback untuk URL yang tidak didukung yt-dlp (mis. direct-link file host
    seperti sfile atau media storage lain yang cuma serve file mentah lewat HTTP).
    Tidak berlaku untuk Terabox dkk yang butuh token/signature hasil render JS.

    Untuk file besar yang server-nya support Range (accept-ranges: bytes),
    otomatis pakai multi-connection (aria2c) karena banyak file host nge-throttle
    bandwidth per-koneksi (~100-300 KB/s) -- dengan koneksi paralel total jadi
    kelipatan. Fallback ke single-connection bila server tak support Range.
    """
    loop = asyncio.get_running_loop()

    def probe():
        prox = requests_proxies()
        attempts = [None] + ([prox] if prox else [])
        for attempt_idx, proxy in enumerate(attempts):
            try:
                r = requests.head(url, timeout=30, allow_redirects=True,
                                  headers=headers or {}, stream=True,
                                  proxies=proxy)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                length = int(r.headers.get("content-length", "0") or 0)
                ranges = r.headers.get("accept-ranges", "").lower()
                return {
                    "length": length,
                    "ranges": ranges,
                    "html": "text/html" in ctype,
                    "status": r.status_code,
                }
            except Exception:
                if attempt_idx >= len(attempts) - 1:
                    return None

    info = await asyncio.to_thread(probe)

    # Pakai aria2 multi-connection kalau file besar & server support range & bukan HTML
    if (
        info
        and not info.get("html")
        and info.get("ranges") == "bytes"
        and info.get("length", 0) >= ARIA2_MIN_BYTES
    ):
        result = await _aria2_multi_download(url, work_dir, ctx, headers=headers,
                                             total=info.get("length"))
        if result:
            return result
        # kalau aria2 gagal/absent, lanjut ke single-connection di bawah

    def do_download():
        # direct-first; retry sekali lewat proxy kalau koneksi langsung gagal
        prox = requests_proxies()
        attempts = [None] + ([prox] if prox else [])
        for attempt_idx, proxy in enumerate(attempts):
            try:
                with requests.get(url, stream=True, timeout=60, headers=headers or {},
                                  proxies=proxy) as r:
                    r.raise_for_status()

                    # kalau yang balik itu halaman HTML (bukan file beneran), berarti ini
                    # bukan direct-link -- kemungkinan shortlink/share-page yang butuh JS
                    # buat nampilin link asli. Gagal jelas di sini, jangan sampe kepupuk
                    # ke-upload sebagai "file" yang isinya cuma halaman web.
                    content_type = r.headers.get("content-type", "").lower()
                    if "text/html" in content_type:
                        raise Exception(
                            "URL ini balikin halaman HTML, bukan file langsung. "
                            "Kemungkinan ini shortlink/share-page yang butuh proses render JS "
                            "(bukan direct-download link) -- yt-dlp juga nggak berhasil extract dari sini."
                        )

                    # coba ambil nama file dari header, fallback ke bagian akhir URL
                    cd = r.headers.get("content-disposition", "")
                    match = re.search(r'filename="?([^";]+)"?', cd)
                    if match:
                        fname = clean_filename(match.group(1))
                    else:
                        fname = clean_filename(url.split("/")[-1].split("?")[0]) or "file.bin"

                    filepath = os.path.join(work_dir, fname)
                    total = int(r.headers.get("content-length", 0))
                    downloaded = 0
                    start = time.monotonic()
                    last_update = [0.0]

                    with open(filepath, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.monotonic()
                            if now - last_update[0] < 2.5 and downloaded < total:
                                continue
                            last_update[0] = now

                            elapsed = max(now - start, 0.001)
                            speed = downloaded / elapsed
                            percent = (downloaded / total * 100) if total else 0
                            asyncio.run_coroutine_threadsafe(
                                render_status(
                                    ctx, "Download", percent=percent,
                                    processed=downloaded, total=total, speed=speed,
                                ),
                                loop,
                            )

                    return filepath
            except Exception:
                if attempt_idx >= len(attempts) - 1:
                    raise
                try:
                    for leftover in (filepath, filepath + ".aria2"):
                        if os.path.isfile(leftover):
                            os.remove(leftover)
                except OSError:
                    pass

    return await asyncio.to_thread(do_download)
