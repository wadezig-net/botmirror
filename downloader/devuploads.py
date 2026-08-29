import os

from config import DEVUPLOADS_SCRIPT, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

DEVUPLOADS_DOMAINS = ("devuploads.com",)


async def devuploads_download(url, work_dir, ctx):
    """
    Devuploads adalah file-host dengan countdown + tombol download (mirip
    sfile). Link file asli cuma keluar setelah halaman dirender JS. Browser
    headless cuma dipakai buat NEMUIN link file + cookies session, filenya
    didownload pakai requests biasa lewat download_resolved_link.
    """
    await render_status(ctx, "🌐 Membuka halaman Devuploads")

    if not os.path.isfile(DEVUPLOADS_SCRIPT):
        raise Exception(
            f"Script {DEVUPLOADS_SCRIPT} tidak ditemukan. "
            "Pastikan sudah di-setup (lihat instruksi setup Playwright)."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    result = await run_node_link_finder(
        NODE_BIN, DEVUPLOADS_SCRIPT, url, work_dir, ctx,
        timeout=180,
    )
    return await download_resolved_link(result, work_dir, ctx)