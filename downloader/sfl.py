import os

from config import BASE_DIR, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

SFL_SCRIPT = f"{BASE_DIR}/scripts/sfl_download.js"

# sfl.gl -- gerbang iklan bertingkat, tujuan akhirnya biasanya link sfile.mobi/sfile.co
SFL_DOMAINS = ("sfl.gl",)


async def sfl_headless_download(url, work_dir, ctx):
    """
    sfl.gl bukan file-host beneran -- dia gerbang iklan (scroll+klik+tunggu timer,
    berulang lewat beberapa tab) yang ujungnya ngarah ke situs file-host lain
    (biasanya sfile.mobi). Script node-nya cuma nembus gerbang itu dan balikin
    URL tujuan akhir, lalu kita serahkan ke handler sfile yang udah ada.
    """
    await render_status(ctx, "🌐 Membuka headless browser (melewati gerbang iklan)")

    if not os.path.isfile(SFL_SCRIPT):
        raise Exception(f"Script {SFL_SCRIPT} tidak ditemukan.")
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    # proses ini bisa makan waktu lumayan lama (beberapa putaran gerbang iklan,
    # tiap putaran ada timer ~15-25 detik) -- kasih timeout lebih longgar
    result = await run_node_link_finder(NODE_BIN, SFL_SCRIPT, url, work_dir, ctx, timeout=240)

    # import lokal (bukan di top-level) buat hindari circular import, karena
    # downloader/sfile.py sendiri nggak butuh import balik dari modul ini
    from downloader.sfile import sfile_headless_download, SFILE_DOMAINS

    redirect_url = result.get("redirect_to")
    if redirect_url:
        await render_status(ctx, "🔗 Sampai di tujuan akhir, lanjut proses sfile")
        if any(d in redirect_url for d in SFILE_DOMAINS):
            return await sfile_headless_download(redirect_url, work_dir, ctx)
        # kalau ternyata tujuan akhirnya bukan sfile (situs lain yang belum kita
        # kenal), tetep coba anggap sebagai direct link biasa
        result = {"direct_url": redirect_url, "referer": url, "filename": None}

    return await download_resolved_link(result, work_dir, ctx)
