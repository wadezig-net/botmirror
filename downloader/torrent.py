import os
import re
import time
import asyncio
import zipfile
from typing import List

import requests

from status_ui import render_status

# batas toleransi "gak ada progress" sebelum task dinyatakan mati/gagal
WATCHDOG_TIMEOUT = 600  # 10 menit

# tracker publik yang masih hidup, ditambah otomatis biar peluang nemu peer lebih besar
# (tracker di magnet user sering mati, mis. rarbg yang tutup 2023)
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.tamersunion.org:443/announce",
]

# regex baris progress aria2c saat download BENERAN jalan, contoh:
# [ #f21c2b 1.0MiB/10.0MiB(10%) CN:4 DL:1.0MiB ETA:8s ]
ARIA_PROGRESS_RE = re.compile(
    r"\[#?\w+\s+([\d.]+[\w.]+)/([\d.]+[\w.]+)\((\d+)%\)\s+CN:\d+(?:\s+SD:\d+)?\s+DL:([\d.]+[\w.]+)?"
)

# regex baris summary saat aria2c masih nunggu peer / fase metadata, contoh:
# [ #1a7c99 0B/0B CN:0 SD:0 DL:0B ]
# (tidak ada persen, total belum diketahui) -- jauh berbeda formatnya dari progres
# biasa, makanya regex terpisah.
ARIA_SUMMARY_RE = re.compile(
    r"\[#?\w+\s+([\d.]+[\w.]+)/([\d.]+[\w.]+)\s+CN:(\d+)\s+SD:(\d+)\s+DL:([\d.]+[\w.]+)?\]"
)

# regex baris "File: path" yang dikeluarkan aria2c sebelum download dimulai
ARIA_FILE_RE = re.compile(r"^\d{2}/\d{2} \d{2}:\d{2}:\d{2} \[NOTICE\] Downloading:\s+(\S+)$", re.M)


def _parse_size(s):
    """'10MiB' -> bytes"""
    match = re.match(r"([\d.]+)\s*([KMGT]?i?B)", s)
    if not match:
        return None
    num, unit = float(match.group(1)), match.group(2).upper()
    mult = {
        "B": 1, "KB": 1024, "KIB": 1024,
        "MB": 1024**2, "MIB": 1024**2,
        "GB": 1024**3, "GIB": 1024**3,
        "TB": 1024**4, "TIB": 1024**4,
    }
    return num * mult.get(unit, 1)


async def download_torrent(magnet, work_dir, ctx) -> List[str]:
    """Download torrent/magnet via aria2c ke work_dir. Return daftar file hasil."""
    total_size = None

    cmd = [
        "aria2c",
        "--dir", work_dir,
        "--seed-time=0",          # jangan seeder lama-lama, langsung kelar
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--summary-interval=1",
        "--console-log-level=warn",
        "--file-allocation=none",
        "--enable-color=false",
        "--bt-max-peers=100",
    ]

    # tracker publik tambahan https:// punya trackers list
    for tracker in PUBLIC_TRACKERS:
        cmd += ["--bt-tracker", tracker]

    cmd.append(magnet)

    # PM2 menyuntikkan env var IPC ke proses ini (lihat ytdlp.py untuk detail).
    # Buang biar nggak bocor ke subprocess aria2c.
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

    last_update = [0.0]      # throttle update progress (2.5s)
    last_wait_update = [0.0] # throttle update status "nunggu peer" (5s)
    last_progress = time.monotonic()  # watchdog: kapan terakhir ada byte ke-download
    timed_out = False
    downloaded_files: List[str] = []

    try:
        async for raw_line in process.stdout:
            line = raw_line.decode(errors="ignore")
            print(line, end="")

            # cari baris "Downloading: <path>" -> ini nama file yang sedang
            # didownload (muncul sekali tiap file di awal).
            m = ARIA_FILE_RE.search(line)
            if m:
                downloaded_files.append(m.group(1))
                continue

            if "[NOTICE]" in line and "Download complete" in line:
                # baris selesai per file; path tertulis setelah pesan
                m = re.search(r"Download complete: (\S+)", line)
                if m:
                    downloaded_files.append(m.group(1))

            # progress umum (download beneran jalan)
            m = ARIA_PROGRESS_RE.search(line)
            if m:
                percent = float(m.group(3))
                file_total = _parse_size(m.group(2))
                speed = _parse_size(m.group(4)) if m.group(4) else None
                if file_total:
                    total_size = file_total

                # ada byte yang turun -> reset watchdog
                if file_total and file_total > 0 and percent > 0:
                    last_progress = time.monotonic()

                now = time.monotonic()
                if now - last_update[0] < 2.5 and percent < 100:
                    continue
                last_update[0] = now

                processed = (total_size * percent / 100) if total_size else None
                await render_status(
                    ctx, "Torrent", percent=percent,
                    processed=processed, total=total_size,
                    speed=speed,
                )
                continue

            # fase metadata / nunggu peer: belum ada progress apapun, tapi bot
            # harus tetap kelihatan "hidup" (sebelumnya panel diem 0% selamanya).
            m = ARIA_SUMMARY_RE.search(line)
            if m:
                # kalau masih belum ada byte sama sekali, tampilkan status menunggu
                dl = _parse_size(m.group(5)) if m.group(5) else 0
                if (dl is None or dl == 0) and not total_size:
                    now = time.monotonic()
                    if now - last_wait_update[0] < 5:
                        continue
                    last_wait_update[0] = now
                    await render_status(
                        ctx, "🧲 Mencari peer/seeder...",
                        percent=ctx.get("percent", 0.0),
                    )

            # watchdog: kalau sekian menit tanpa ada byte yang turun, anggap
            # torrent-nya mati/gak ada seeder -- jangan biarkan task menggantung.
            if time.monotonic() - last_progress > WATCHDOG_TIMEOUT:
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass
                break

        await process.wait()
    finally:
        ctx["process"] = None

    if timed_out:
        raise Exception(
            f"⏱ Timeout {WATCHDOG_TIMEOUT // 60} menit tanpa progress "
            "(tracker nggak menemukan peer/seeder). Link torrent mungkin mati."
        )

    if process.returncode != 0:
        raise Exception("aria2c gagal mendownload torrent. Cek pm2 logs.")

    # kalau parsing nama file gagal, ambil file dari work_dir sebagai fallback
    if not downloaded_files:
        downloaded_files = [
            os.path.join(work_dir, f)
            for f in os.listdir(work_dir)
        ]

    return downloaded_files


def zip_folder(work_dir: str, ctx) -> str:
    """Zip seluruh isi work_dir jadi satu arsip .torrent.zip di work_dir. Return path."""
    archive_name = os.path.join(work_dir, "torrent_download.zip")

    with zipfile.ZipFile(archive_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(work_dir):
            for f in files:
                if f == os.path.basename(archive_name):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, work_dir)
                zf.write(full, arcname=rel)

    return archive_name


async def fetch_torrent_file(torrent_url: str, work_dir: str) -> str:
    """Download file .torrent dari URL ke work_dir, return path lokalnya.

    Jalan di thread supaya nggak nge-block event loop. Nama file simpel
    'input.torrent' biar gak ribet dengan query string / nama aneh.
    """
    dest = os.path.join(work_dir, "input.torrent")

    def _download():
        resp = requests.get(torrent_url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        return dest

    return await asyncio.to_thread(_download)
