import os

from config import FICHIER_SCRIPT, NODE_BIN, FICHIER_LOGIN_COOKIES, FICHIER_PROXIES_FILE
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

FICHIER_DOMAINS = ("1fichier.com",)


async def fichier_headless_download(url, work_dir, ctx):
    """
    1fichier (khusus free user) butuh klik tombol download, tunggu countdown,
    lalu klik lagi (kadang di tab baru) sebelum link asli ke-generate. Kalau
    ada file cookies login Premium, dipakai dulu biar lolos rate-limit &
    dapat speed maksimal. Kalau nggak ada, coba rotasi proxy dari daftar
    (kalau ada) tiap kali kena rate-limit "must wait".
    """
    await render_status(ctx, "🌐 Membuka headless browser")

    if not os.path.isfile(FICHIER_SCRIPT):
        raise Exception(
            f"Script {FICHIER_SCRIPT} tidak ditemukan. "
            "Pastikan sudah di-setup (lihat instruksi setup Playwright)."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    extra_env = {}
    if FICHIER_LOGIN_COOKIES and os.path.isfile(FICHIER_LOGIN_COOKIES):
        extra_env["FICHIER_LOGIN_COOKIES"] = FICHIER_LOGIN_COOKIES
    if FICHIER_PROXIES_FILE and os.path.isfile(FICHIER_PROXIES_FILE):
        extra_env["FICHIER_PROXIES_FILE"] = FICHIER_PROXIES_FILE

    result = await run_node_link_finder(
        NODE_BIN, FICHIER_SCRIPT, url, work_dir, ctx,
        timeout=300,
        extra_env=extra_env or None,
    )
    return await download_resolved_link(result, work_dir, ctx)


async def get_fichier_direct_link(url, work_dir, ctx):
    """
    Sama seperti fichier_headless_download, tapi berhenti begitu dapat
    direct_url -- TIDAK ikut download file-nya (link 1fichier keburu
    dianggap expired/410 kalau di-fetch ulang terpisah pakai requests).
    Dipakai buat kasus user cuma mau linknya, bukan file-nya.
    """
    await render_status(ctx, "🌐 Membuka headless browser")

    if not os.path.isfile(FICHIER_SCRIPT):
        raise Exception(f"Script {FICHIER_SCRIPT} tidak ditemukan.")
    if not os.path.isfile(NODE_BIN):
        raise Exception(f"Node binary tidak ditemukan di {NODE_BIN}.")

    extra_env = {}
    if FICHIER_LOGIN_COOKIES and os.path.isfile(FICHIER_LOGIN_COOKIES):
        extra_env["FICHIER_LOGIN_COOKIES"] = FICHIER_LOGIN_COOKIES
    if FICHIER_PROXIES_FILE and os.path.isfile(FICHIER_PROXIES_FILE):
        extra_env["FICHIER_PROXIES_FILE"] = FICHIER_PROXIES_FILE

    return await run_node_link_finder(
        NODE_BIN, FICHIER_SCRIPT, url, work_dir, ctx,
        timeout=300,
        extra_env=extra_env or None,
    )
