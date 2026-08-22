import os

from config import SFILE_SCRIPT, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

# domain yang butuh resolusi khusus lewat headless browser sebelum bisa di-download
SFILE_DOMAINS = ("sfile.co", "sfile.mobi")


async def sfile_headless_download(url, work_dir, ctx):
    """
    sfile.co nggak nyimpen link download langsung di HTML -- link itu ke-generate
    lewat JS (kadang auto, kadang butuh klik 2x di 2 halaman berturut, kadang
    lewat tab baru). Browser headless cuma dipakai buat NEMUIN link file asli
    (lewat traffic network) + cookies session, filenya sendiri didownload
    langsung pakai requests biasa (lebih stabil daripada save dari browser).
    """
    await render_status(ctx, "🌐 Membuka headless browser")

    if not os.path.isfile(SFILE_SCRIPT):
        raise Exception(
            f"Script {SFILE_SCRIPT} tidak ditemukan. "
            "Pastikan sudah di-setup (lihat instruksi setup Playwright)."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    result = await run_node_link_finder(NODE_BIN, SFILE_SCRIPT, url, work_dir, ctx)
    return await download_resolved_link(result, work_dir, ctx)
