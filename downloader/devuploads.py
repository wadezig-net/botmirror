import os

from config import DEVUPLOADS_SCRIPT, NODE_BIN, next_proxy
from status_ui import render_status
from downloader.browser_link_capture import (
    run_node_link_finder,
    download_resolved_link_aria2,
)

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

    # Gunakan proxy dari pool buat melewati geo-block datacenter VPS.
    # Devuploads sering nolak IP datacenter; proxy mengarahkan lewat IP berbeda.
    proxy = next_proxy()
    extra_env = {"DOWNLOAD_PROXY": proxy} if proxy else None
    result = await run_node_link_finder(
        NODE_BIN, DEVUPLOADS_SCRIPT, url, work_dir, ctx,
        timeout=180,
        extra_env=extra_env,
    )
    # Devuploads nge-throttle free-user. Dulu per-koneksi (~1 Mbps) dan paralel
    # aria2 bantu (kali lipat); sekarang yang sering terjadi throttle PER-IP
    # global (~1.0-1.1 MiB/s), jadi koneksi sebanyak apa pun sama saja. Karena
    # itu strateginya: coba 2x resolve untuk dapet node yang kenceng; kalau
    # semua lemot, JANGAN gagal -- cukup selesaikan secepat server mau kasih
    # (lambat tapi file-nya tetap jadi).
    for attempt in (1, 2):
        await render_status(
            ctx, f"🌐 Mencari node download Devuploads (percobaan {attempt}/2)"
        )
        if attempt > 1:
            proxy = next_proxy()
            extra_env = {"DOWNLOAD_PROXY": proxy} if proxy else None
            result = await run_node_link_finder(
                NODE_BIN, DEVUPLOADS_SCRIPT, url, work_dir, ctx,
                timeout=180,
                extra_env=extra_env,
            )
        filepath = await download_resolved_link_aria2(
            result, work_dir, ctx,
            connections=12,
            min_speed_bps=1.5 * 1024 * 1024,  # jauh di bawah kecepatan paralel normal
            slow_window_s=25,
        )
        if filepath:
            return filepath
        await render_status(ctx, "♻️ Node lambat, ganti node baru...")

    # Semua node lemot (throttle per-IP). Tetap lanjut download tanpa watchdog
    # biar file-nya jadi, walau lambat (~1 MiB/s).
    await render_status(
        ctx, "⚠️ Semua node Devuploads lagi lambat (per-IP throttle); download diteruskan apa adanya..."
    )
    filepath = await download_resolved_link_aria2(
        result, work_dir, ctx,
        connections=12,
    )
    if filepath:
        return filepath

    raise Exception(
        "Gagal download dari Devuploads. Server lagi overload / throttle IP "
        "untuk file ini; coba lagi beberapa menit lagi."
    )