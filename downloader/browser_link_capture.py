import os
import re
import json
import time
import shutil
import asyncio
import requests

from status_ui import render_status
from utils import clean_filename

# regex baris progress aria2c (--console-log-level=notice --summary-interval=2):
#   [#a1b2c3 45.2MiB/141MiB(32%) CN:8 DL:2.4MiB ETA:0s]
_ARIA2_PROGRESS_RE = re.compile(
    r"\[#\w+\s+([\d.]+)[KMG]iB/[\d.]+[KMG]iB\((\d+)%\)\s+CN:(\d+)\s+DL:([\d.]+)([KMG])iB"
)

# mapping pola baris [debug] dari script node -> pesan status yang enak dibaca user,
# biar panel di Telegram keliatan "hidup" selama proses browser headless jalan
_PHASE_PATTERNS = [
    (re.compile(r"navigasi ke"), "🌐 Membuka halaman"),
    (re.compile(r"tombol download visible\?\s*true"), "🖱️ Tombol download ketemu"),
    (re.compile(r"percobaan klik ke-(\d+)"), "🖱️ Mencoba klik download (percobaan {0})"),
    (re.compile(r"kandidat (file|media) ketemu"), "🔎 Kandidat link ditemukan, memverifikasi..."),
    (re.compile(r"dlink (ketemu|dari API|dari traffic)"), "🔗 Link download Terabox ketemu"),
    (re.compile(r"popup baru terbuka|\[popup\d+\] response"), "🪟 Memproses tab tambahan"),
    (re.compile(r"meta tag hasil"), "🏷️ Membaca metadata halaman"),
    (re.compile(r"pakai video dari|pakai gambar dari"), "🔎 Media ditemukan"),
]


def _phase_from_debug_line(line):
    for pattern, label in _PHASE_PATTERNS:
        match = pattern.search(line)
        if match:
            try:
                return label.format(*match.groups())
            except (IndexError, KeyError):
                return label
    return None


async def _heartbeat(ctx, stop_event):
    """Update panel tiap beberapa detik walau nggak ada progress baru yang jelas,
    minimal system stats & elapsed time kelihatan jalan -- biar user tau bot
    masih hidup, bukan macet, selama browser headless bekerja di background."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4)
        except asyncio.TimeoutError:
            await render_status(ctx, ctx.get("phase", "🌐 Memproses headless browser"))


async def run_node_link_finder(node_bin, script_path, url, work_dir, ctx, timeout=120, extra_env=None):
    """
    Jalankan script node yang tugasnya cuma NEMUIN link media asli (bukan
    download-nya) + cookies session. Selama proses ini jalan (bisa puluhan
    detik), status di Telegram di-update berkala biar nggak keliatan freeze.
    Return: dict hasil parse JSON dari stdout script node.
    """
    proc_env = {**os.environ, **extra_env} if extra_env else None
    proc = await asyncio.create_subprocess_exec(
        node_bin, script_path, url, work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=proc_env,
    )
    ctx["process"] = proc

    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(ctx, stop_heartbeat))

    async def read_stderr():
        async for raw_line in proc.stderr:
            line = raw_line.decode(errors="ignore").strip()
            if not line:
                continue
            print(line)  # tetep kecatet di pm2 logs buat debugging
            phase = _phase_from_debug_line(line)
            if phase:
                await render_status(ctx, phase)

    try:
        stderr_task = asyncio.create_task(read_stderr())
        try:
            # baca stdout manual (BUKAN proc.communicate()) -- communicate() bakal
            # baca stderr juga secara internal, bentrok sama stderr_task di atas
            # yang udah baca stream itu duluan -> error "read() called while
            # another coroutine is already waiting for incoming data"
            stdout = await asyncio.wait_for(proc.stdout.read(), timeout=timeout)
            await proc.wait()
        finally:
            await stderr_task
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise Exception(
            f"Headless browser timeout (>{timeout}s) saat mencari link. "
            f"Cek manual: {node_bin} {script_path} <url> /tmp"
        )
    finally:
        stop_heartbeat.set()
        await heartbeat_task
        ctx["process"] = None

    output = stdout.decode(errors="ignore").strip()
    try:
        result = json.loads(output.splitlines()[-1])
    except Exception:
        raise Exception(f"Gagal parse output headless browser: {output[:500]}")

    if not result.get("ok"):
        raise Exception(f"Headless browser gagal menemukan link: {result.get('error')}")

    return result


async def download_resolved_link(result, work_dir, ctx):
    """Download file dari hasil run_node_link_finder() (direct_url + cookies) pakai requests biasa."""
    await render_status(ctx, "🔗 Link ketemu, mendownload langsung")

    direct_url = result["direct_url"]
    filename = clean_filename(result.get("filename") or "downloaded_file.bin")
    referer = result.get("referer", direct_url)
    cookie_file = result.get("cookie_file")

    cookies = {}
    if cookie_file and os.path.isfile(cookie_file):
        try:
            with open(cookie_file) as f:
                for c in json.load(f):
                    cookies[c["name"]] = c["value"]
        except Exception:
            pass  # kalau gagal parse cookies, tetep coba download tanpa cookies

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }

    filepath = os.path.join(work_dir, filename)
    loop = asyncio.get_running_loop()

    def do_download():
        start = time.monotonic()
        last_update = [0.0]
        with requests.get(direct_url, headers=headers, cookies=cookies, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
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

    return await asyncio.to_thread(do_download)


async def _aria2_parse_line(line):
    """Terjemahkan satu baris summary aria2c jadi (label, percent, processed, total, speed, eta)."""
    m = _ARIA2_PROGRESS_RE.search(line)
    if not m:
        return None
    val, percent, _conn, dl, unit = (
        float(m.group(1)),
        float(m.group(2)),
        int(m.group(3)),
        float(m.group(4)),
        m.group(5),
    )
    processed = val * {"K": 1024, "M": 1024**2, "G": 1024**3}[unit]
    speed = dl * {"K": 1024, "M": 1024**2, "G": 1024**3}[unit]
    return percent, processed, speed


async def download_resolved_link_aria2(result, work_dir, ctx, connections=8):
    """
    Variant download untuk host yang nge-throttle bandwidth per-koneksi (mis.
    Devuploads: free user ~1 Mbps/koneksi). URL yang mendukung Range (server
    balas 206) bisa dipartisi jadi beberapa koneksi paralel -- total bandwidth
    jadi kelipatan hingga ~8-16x. Butuh aria2c terinstal; pakai internal parser
    progress biar panel Telegram tetap update.
    """
    aria2 = shutil.which("aria2c")
    if not aria2:
        return await download_resolved_link(result, work_dir, ctx)

    direct_url = result["direct_url"]
    filename = clean_filename(result.get("filename") or "downloaded_file.bin")
    referer = result.get("referer", direct_url)

    await render_status(ctx, f"🔗 Mendownload {connections} koneksi paralel (aria2c)")

    filepath = os.path.join(work_dir, filename)
    cmd = [
        aria2,
        "--no-conf",
        "--summary-interval", "2",
        "--console-log-level=notice",
        "-x", str(connections),
        "-s", str(connections),
        "-k", "1M",
        "--dir", work_dir,
        "--out", filename,
        "--file-allocation=none",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--header", f"Referer: {referer}",
        "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        direct_url,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    ctx["process"] = process
    dl_last_update = [0.0]

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
                processed=processed, speed=speed,
            )
        await process.wait()
    finally:
        ctx["process"] = None

    if process.returncode != 0 or not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        # gagal paralel (mis. server nolak Range) -> fallback single-connection
        return await download_resolved_link(result, work_dir, ctx)

    return filepath
