import os
import re
import time
import asyncio
import sys
from urllib.parse import urlparse

from config import COOKIE_FILE
from status_ui import render_status

YTDLP_BIN = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "yt-dlp")

# regex buat baris progress yt-dlp, contoh:
# [download]  45.2% of  120.50MiB at    2.34MiB/s ETA 00:30
DL_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\S+).*?at\s+([\d.]+\s*\S+/s|Unknown speed)(?:\s+ETA\s+(\S+))?"
)


def parse_size_str(s):
    """Parse '120.50MiB' -> bytes (float)."""
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


async def download_via_url(url, work_dir, ctx):
    """yt-dlp dulu; kalau situsnya nggak didukung, fallback ke headless browser
    (khusus sfile/threads) atau generic HTTP direct-download."""
    domain = urlparse(url).netloc.lower()

    # sfl.gl: gerbang iklan berlapis + captcha yang berubah-ubah -- sengaja didesain
    # susah diotomasi. Daripada bot nyoba-coba terus gagal diam-diam / nyasar ke
    # halaman iklan random, kasih pesan jelas dari awal biar user tau harus
    # resolve manual dulu (buka link-nya sendiri, lewatin iklan+captcha, ambil
    # link tujuan akhirnya -- biasanya sfile.mobi -- baru /mirror link itu).
    if "sfl.gl" in domain:
        raise Exception(
            "Link sfl.gl nggak didukung otomatis (situsnya pakai captcha berubah-ubah "
            "yang sengaja mencegah bot). Buka link ini manual di browser, lewati "
            "iklan+captcha-nya, lalu /mirror link TUJUAN AKHIRNYA (biasanya sfile.mobi)."
        )

    # import lokal untuk hindari circular import (fallback butuh modul lain
    # yang juga mengimpor dari sini secara tidak langsung)
    from downloader.http_direct import generic_http_download
    from downloader.sfile import sfile_headless_download, SFILE_DOMAINS
    from downloader.threads import threads_headless_download, THREADS_DOMAINS
    from downloader.fichier import fichier_headless_download, FICHIER_DOMAINS
    from downloader.mega import mega_download, MEGA_DOMAINS

    output_template = f"{work_dir}/%(title)s.%(ext)s"

    cmd = [
        YTDLP_BIN,
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "-f", "bestvideo[height<=1080]+bestaudio/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--newline",
        # HLS/m3u8 stream (TikTok, IG, dll) suka gagal ambil fragment terakhir
        # kalau cuma retry beberapa kali doang -> paksa retry terus + kasih jeda
        # antar-percobaan, biar nggak asal skip fragment dan hasil jadi kepotong.
        "--fragment-retries", "infinite",
        "--retry-sleep", "fragment:2",
        "--retries", "10",
        "--extractor-retries", "5",
        # reconnect otomatis kalau koneksi ke server drop di tengah fragment
        "--downloader-args", "ffmpeg:-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        # mitigasi buat bug TikTok "Unexpected response from webpage request" yang lagi
        # rame dilaporin ke yt-dlp (issue #17403 dkk, per Agustus 2026, belum ada fix resmi).
        # --force-ipv4 kadang membantu karena beberapa report nunjukin masalahnya terkait
        # fingerprinting koneksi IPv6.
        "--force-ipv4",
        "-o", output_template,
    ]

    # TikTok kadang nge-403 kalau kita kirim cookies.txt yang isinya bukan cookies
    # TikTok yang valid (dianggap "logged-in tapi mencurigakan"). Situs lain (YouTube dkk)
    # tetap butuh cookies buat konten age-restricted, jadi cuma di-skip khusus TikTok.
    is_tiktok = "tiktok.com" in domain
    if os.path.isfile(COOKIE_FILE) and not is_tiktok:
        cmd += ["--cookies", COOKIE_FILE]

    cmd.append(url)

    downloaded_file = None
    dl_last_update = [0.0]  # throttle biar nggak spam edit_text -> kena FLOOD_WAIT

    # PM2 menyuntikkan env var IPC (NODE_CHANNEL_FD, dll) ke proses yang dia jalankan.
    # Kalau ini ikut diwariskan ke subprocess deno/node yang dipanggil yt-dlp untuk
    # menyelesaikan JS challenge YouTube, deno/node akan salah sangka mereka dipanggil
    # sebagai child process ber-IPC dan crash dengan "fd is not from BiPipe".
    # Fix: buang env var terkait node-IPC sebelum spawn subprocess yt-dlp.
    clean_env = os.environ.copy()
    for var in ("NODE_CHANNEL_FD", "NODE_UNIQUE_ID", "NODE_CHANNEL_SERIALIZATION_MODE"):
        clean_env.pop(var, None)

    # subprocess async supaya event loop tidak ke-block selama download
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=clean_env,
    )
    ctx["process"] = process

    try:
        async for raw_line in process.stdout:
            line = raw_line.decode(errors="ignore")
            print(line, end="")

            if "[download]" in line and "%" in line:
                match = DL_PROGRESS_RE.search(line)
                if match:
                    percent = float(match.group(1))

                    # throttle: yt-dlp bisa ngeluarin baris progress berkali-kali per detik
                    # (apalagi koneksi kenceng) -- edit_text sesering itu bikin Telegram
                    # ngasih FLOOD_WAIT dan panel jadi macet lama. Update paling cepat tiap 2.5 detik,
                    # kecuali pas capai 100% (biar transisi ke fase berikutnya tetap kelihatan).
                    now = time.monotonic()
                    if now - dl_last_update[0] < 2.5 and percent < 100:
                        continue
                    dl_last_update[0] = now

                    total = parse_size_str(match.group(2))
                    speed_str = match.group(3)
                    eta = match.group(4)
                    speed = parse_size_str(speed_str.replace("/s", "")) if "/s" in speed_str else None
                    processed = (total * percent / 100) if total else None
                    await render_status(
                        ctx, "Download", percent=percent,
                        processed=processed, total=total,
                        speed=speed, eta=eta,
                    )

            if "Destination:" in line:
                downloaded_file = line.split("Destination:")[-1].strip()

            # yt-dlp download video & audio sebagai 2 stream terpisah (masing2 0-100%
            # sendiri), lalu digabung pakai ffmpeg -- proses gabung ini nggak ngeluarin
            # baris progress sama sekali, jadi kelihatan "macet" kalau nggak dikasih tau.
            for keyword, label in (
                ("[Merger]", "Menggabungkan video + audio"),
                ("[FixupM3u8]", "Memperbaiki container video"),
                ("[ExtractAudio]", "Mengekstrak audio"),
                ("[VideoConvertor]", "Mengonversi video"),
                ("[Metadata]", "Menulis metadata"),
            ):
                if keyword in line:
                    await render_status(ctx, f"⚙️ {label}...")
                    break

        await process.wait()
    finally:
        ctx["process"] = None

    if process.returncode != 0:
        if downloaded_file is None:
            domain = urlparse(url).netloc.lower()
            await render_status(ctx, "⚠️ Bukan situs yt-dlp, mencoba direct download")
            try:
                if any(d in domain for d in SFILE_DOMAINS):
                    downloaded_file = await sfile_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in THREADS_DOMAINS):
                    downloaded_file = await threads_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in FICHIER_DOMAINS):
                    downloaded_file = await fichier_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in MEGA_DOMAINS):
                    downloaded_file = await mega_download(url, work_dir, ctx)
                else:
                    downloaded_file = await generic_http_download(url, work_dir, ctx)
            except Exception as fallback_err:
                raise Exception(
                    f"yt-dlp gagal & direct download juga gagal: {fallback_err}"
                )
        else:
            raise Exception("yt-dlp gagal. Cek pm2 logs")

    # kalau parsing "Destination:" gagal nangkep nama file, fallback:
    # cari file terbaru di work_dir (folder ini isolated per-request, jadi aman)
    if not downloaded_file or not os.path.isfile(downloaded_file):
        candidates = [os.path.join(work_dir, f) for f in os.listdir(work_dir)]
        candidates = [f for f in candidates if os.path.isfile(f)]
        if not candidates:
            raise Exception("File hasil download tidak ditemukan")
        downloaded_file = max(candidates, key=os.path.getmtime)

    return downloaded_file
