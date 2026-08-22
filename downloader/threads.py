import os

from config import BASE_DIR, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

THREADS_SCRIPT = f"{BASE_DIR}/scripts/threads_download.js"

# threads.com/threads.net belum didukung yt-dlp per Agustus 2026
# (lihat: https://github.com/yt-dlp/yt-dlp/issues/7523, masih open)
THREADS_DOMAINS = ("threads.com", "threads.net")


async def threads_headless_download(url, work_dir, ctx):
    """
    Threads (Meta) belum didukung yt-dlp. Kita ambil video/gambarnya lewat
    headless browser: coba dari meta tag og:video dulu (paling stabil, nggak
    butuh nunggu render JS), fallback ke pantau traffic network kalau nggak
    ketemu di situ.
    """
    await render_status(ctx, "🌐 Membuka headless browser")

    if not os.path.isfile(THREADS_SCRIPT):
        raise Exception(
            f"Script {THREADS_SCRIPT} tidak ditemukan. "
            "Pastikan sudah di-setup di server."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    result = await run_node_link_finder(NODE_BIN, THREADS_SCRIPT, url, work_dir, ctx)
    return await download_resolved_link(result, work_dir, ctx)
