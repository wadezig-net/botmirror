import os
import re
import time
import asyncio
import sys
import collections
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

    # Terabox, Devuploads, & Kaceku tidak didukung yt-dlp sama sekali -- langsung ke
    # resolver masing-masing tanpa buang waktu coba-coba yt-dlp dulu.
    from downloader.terabox import terabox_download, TERABOX_DOMAINS
    from downloader.devuploads import devuploads_download, DEVUPLOADS_DOMAINS
    from downloader.kaceku import kaceku_download, KACEKU_DOMAINS
    if any(d in domain for d in TERABOX_DOMAINS):
        return await terabox_download(url, work_dir, ctx)
    if any(d in domain for d in DEVUPLOADS_DOMAINS):
        return await devuploads_download(url, work_dir, ctx)
    if any(d in domain for d in KACEKU_DOMAINS):
        return await kaceku_download(url, work_dir, ctx)

    # sfl.gl: gerbang iklan berlapis + captcha yang berubah-ubah -- sengaja didesain
    # susah diotomasi. Kalau CAPTCHA_API_KEY di-set di .env, kita coba lewatin
    # lewat 2Captcha (Turnstile solving) + browser; kalau nggak ada key, kasih
    # pesan jelas dari awal biar user tau harus resolve manual dulu (buka
    # link-nya sendiri, lewatin iklan+captcha, ambil link tujuan akhirnya --
    # biasanya sfile.mobi -- baru /mirror link itu).
    if "sfl.gl" in domain:
        from downloader import captcha as captcha_mod
        if captcha_mod.captcha_configured():
            from downloader.sfl import sfl_captcha_download
            return await sfl_captcha_download(url, work_dir, ctx)
        raise Exception(
            "Link sfl.gl butuh bypass captcha (Turnstile) yang otomatis.\n\n"
            "Set CAPTCHA_API_KEY (dari 2captcha.com) di .env buat bikin bot bisa "
            "lewat otomatis, atau buka link ini manual di browser, lewati "
            "iklan+captcha-nya, lalu /mirror link TUJUAN AKHIRNYA (biasanya sfile.mobi)."
        )

    # Real-Debrid dulu (akun premium umumnya bisa unlock host yang debrid-link
    # free tolak). Kalau RD gagal, lanjut ke jalur berikutnya (bukan langsung
    # menyerah). 1Fichier di-handle di branch FICHIER khusus.
    from downloader.real_debrid import (
        rd_configured,
        RD_DOMAINS,
        real_debrid_download,
    )
    if rd_configured() and any(d in domain for d in RD_DOMAINS) and "1fichier" not in domain:
        try:
            return await real_debrid_download(url, work_dir, ctx)
        except Exception:
            pass

    # Host premium (Rapidgator, Uploaded, Turbobit, keep2share, dll) nggak bisa
    # di-resolve yt-dlp maupun fallback direct-download biasa. Kalau
    # DEBRID_LINK_API_KEY di-set di .env, cegat di sini dan unlock link-nya lewat
    # Debrid-Link biar jadi direct link (mirror juga link 1fichier premium).
    from downloader.debrid_link import (
        debrid_configured,
        DEBRID_LINK_DOMAINS,
        debrid_link_download,
    )
    if debrid_configured() and any(d in domain for d in DEBRID_LINK_DOMAINS):
        return await debrid_link_download(url, work_dir, ctx)

    # Torrent/magnet: qBittorrent (self-debrid style) kalau dikonfigurasi.
    from downloader.qbittorrent import qb_configured, torrent_download
    if url.startswith("magnet:") or url.lower().endswith(".torrent"):
        if qb_configured():
            return await torrent_download(url, work_dir, ctx)
        raise Exception(
            "Link torrent/magnet butuh qBittorrent (self-debrid style). Isi "
            "QB_HOST, QB_PORT, QB_USER, QB_PASS di .env -- web UI qBittorrent "
            "harus aktif di server ini."
        )

    # JDownloader: 100+ hoster premium/free (mediafire, mega, uptobox, dll) via
    # MyJDownloader (self-debrid style). Dicegat sebelum yt-dlp karena host di
    # daftar JD_DOMAINS umumnya gak bisa di-resolve jalur lain.
    from downloader.jdownloader import jd_configured, JD_DOMAINS, jdownloader_download
    if jd_configured() and any(d in domain for d in JD_DOMAINS):
        return await jdownloader_download(url, work_dir, ctx)

    # X/Twitter: yt-dlp extractornya kadang gagal (terutama amplify video / iklan
    # yang media-nya tak ter-expose). Resolver khusus lewat API fxtwitter dulu;
    # kalau gagal, baru lanjut jalur yt-dlp di bawah.
    from downloader.xmedia import x_download, X_DOMAINS
    if any(d in domain for d in X_DOMAINS):
        try:
            return await x_download(url, work_dir, ctx)
        except Exception:
            pass

    # import lokal untuk hindari circular import (fallback butuh modul lain
    # yang juga mengimpor dari sini secara tidak langsung)
    from downloader.http_direct import generic_http_download
    from downloader.sfile import (
        sfile_headless_download,
        sfile_http2_download,
        SFILE_DOMAINS,
    )
    from downloader.threads import threads_headless_download, THREADS_DOMAINS
    from downloader.fichier import fichier_headless_download, FICHIER_DOMAINS
    from downloader.mega import mega_download, MEGA_DOMAINS

    output_template = f"{work_dir}/%(title)s.%(ext)s"

    cmd = [
        YTDLP_BIN,
        "--js-runtimes", "node",
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
        # Downloader HLS paksa ffmpeg. Server fragment YouTube/M3U8 sering nge-403
        # downloader native yt-dlp dari IP datacenter (VPS); ffmpeg dengan reconnect
        # + retry jauh lebih toleran. Proto lain (http/dash) tetap native.
        "--downloader", "ffmpeg:hls",
        # reconnect otomatis kalau koneksi ke server drop di tengah fragment
        "--downloader-args", "ffmpeg:-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -rw_timeout 15000000",
        # YouTube: client yang dipake nentuin ketersediaan format.
        # - tv: HLS penuh (1080p+), nggak butuh po-token.
        # - ios: fallback pertama kalau tv lagi kena eksperimen SABR (#12482).
        # - android: nggak kena SABR & nggak butuh po-token, tapi makan
        #   cookies -> cuma itag 18 (360p) sebagai safety net.
        # web_creator & mweb DIHAPUS karena sekarang wajib GVS PO Token yang
        # nggak kita punya (akan 403 / blank).
        # Catatan: EJS solver itu built-in di yt-dlp, jadi TIDAK pakai
        # --remote-components ejs:github (yang tiap run download dari GitHub --
        # di VPS suka gagal => "challenge solver distribution" hilang).
        "--extractor-args", "youtube:player_client=tv,ios,android",
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
    out_buf = collections.deque(maxlen=500)  # tail output buat diagnosa kegagalan

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
            out_buf.append(line)

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
                    try:
                        # Jalur utama: HTTP/2 ringan tanpa browser. Lebih cepat &
                        # stabil. Fallback ke headless Playwright kalau gagal.
                        downloaded_file = await sfile_http2_download(url, work_dir, ctx)
                    except Exception:
                        downloaded_file = await sfile_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in THREADS_DOMAINS):
                    downloaded_file = await threads_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in FICHIER_DOMAINS):
                    # Urutan jalur unlock 1fichier: Real-Debrid -> Debrid-Link ->
                    # JDownloader -> headless asli. Tiap langkah yang gagal
                    # otomatis turun ke langkah berikutnya.
                    fichier_chain = []
                    if rd_configured():
                        fichier_chain.append(("Real-Debrid", real_debrid_download))
                    if debrid_configured():
                        fichier_chain.append(("Debrid-Link", debrid_link_download))
                    if jd_configured():
                        fichier_chain.append(("JDownloader", jdownloader_download))
                    downloaded_file = None
                    for label, resolver in fichier_chain:
                        try:
                            downloaded_file = await resolver(url, work_dir, ctx)
                            break
                        except Exception:
                            continue
                    if downloaded_file is None:
                        if fichier_chain:
                            await render_status(ctx, "⚠️ Debrid/JDownloader gagal, coba 1fichier asli")
                        downloaded_file = await fichier_headless_download(url, work_dir, ctx)
                elif any(d in domain for d in MEGA_DOMAINS):
                    downloaded_file = await mega_download(url, work_dir, ctx)
                else:
                    # Scrapling fallback universal (StealthyFetcher + capture_xhr)
                    # sebelum menyusut jadi generic HTTP direct-download. Host
                    # yang butuh render JS / anti-bot bisa ter-resolve di sini.
                    try:
                        from downloader.scrapling_resolver import scrapling_try
                        downloaded_file = await scrapling_try(url, work_dir, ctx)
                    except Exception:
                        downloaded_file = await generic_http_download(url, work_dir, ctx)
            except Exception as fallback_err:
                ytdlp_tail = "\n".join(list(out_buf)[-40:])
                try:
                    import os as _os
                    _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs")
                    _os.makedirs(_log_dir, exist_ok=True)
                    with open(_os.path.join(_log_dir, "ytdlp_last_err.log"), "w") as _f:
                        _f.write(ytdlp_tail or "(kosong)")
                except Exception:
                    pass
                raise Exception(
                    f"yt-dlp gagal & direct download juga gagal: {fallback_err}"
                    + (f"\n\n—— yt-dlp ———\n{ytdlp_tail[-2500:]}" if ytdlp_tail else "")
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
