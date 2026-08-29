import os

from config import TERABOX_SCRIPT, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

# domain/alias Terabox yang umum (share link bisa dipakai di banyak mirror)
TERABOX_DOMAINS = (
    "terabox.com", "teraboxapp.com", "1024tera.com", "nephobox.com",
    "terasharelink.com", "mirrobox.com", "momerybox.com", "4funbox.com",
    "dubox.com", "freeterabox.com",
)


async def terabox_download(url, work_dir, ctx):
    """
    Terabox nggak didukung yt-dlp; link download asli ('dlink') cuma keluar
    setelah halaman share dirender JS & API /share/list dipanggil. Script node
    headless cuma dipakai buat NEMUIN dlink + cookies session, filenya
    didownload langsung pakai requests biasa (dlink Terabox gampang expired,
    jadi jangan fetch dua kali).
    """
    await render_status(ctx, "🌐 Membuka halaman Terabox")

    if not os.path.isfile(TERABOX_SCRIPT):
        raise Exception(
            f"Script {TERABOX_SCRIPT} tidak ditemukan. "
            "Pastikan sudah di-setup (lihat instruksi setup Playwright)."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    result = await run_node_link_finder(
        NODE_BIN, TERABOX_SCRIPT, url, work_dir, ctx,
        timeout=180,
    )
    return await download_resolved_link(result, work_dir, ctx)