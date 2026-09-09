import os
import json
import asyncio

from config import SFILE_SCRIPT, SFILE_HTTP2_SCRIPT, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import (
    run_node_link_finder,
    download_resolved_link,
    _heartbeat,
)

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


async def sfile_http2_download(url, work_dir, ctx, timeout=90):
    """
    Jalur paling ringan & stabil buat sfile: TANPA browser. Langsung pakai
    HTTP/2 (node:http2) versi Chrome 131 untuk:
      1. buka halaman share -> baca data-dw-url
      2. buka halaman download -> baca data-direct-download
      3. generate cookie _pid (fingerprint) biar server attach cookies download
      4. follow 302 -> CDN, lalu unduh file langsung.

    Return: path file yang berhasil didownload.
    """
    if not os.path.isfile(SFILE_HTTP2_SCRIPT):
        raise Exception(
            f"Script {SFILE_HTTP2_SCRIPT} tidak ditemukan. "
            "Cek konfigurasi SFILE_HTTP2_SCRIPT di config.py."
        )
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    await render_status(ctx, "🟢 Resolve sfile via HTTP/2 (tanpa browser)")

    fname = os.path.basename(url.split("/")[-1].split("?")[0]) or "sfile_download"
    out_file = os.path.join(work_dir, fname)

    # buang env node-IPC biar subprocess jalan bersih (sama kaya di runner lain)
    clean_env = os.environ.copy()
    for var in ("NODE_CHANNEL_FD", "NODE_UNIQUE_ID", "NODE_CHANNEL_SERIALIZATION_MODE"):
        clean_env.pop(var, None)

    loop = asyncio.get_running_loop()

    def do_run():
        import subprocess
        proc = subprocess.run(
            [NODE_BIN, SFILE_HTTP2_SCRIPT, "download", url, out_file],
            capture_output=True, text=True, timeout=timeout,
            env=clean_env,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"sfile HTTP/2 gagal: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        out = proc.stdout.strip()
        if not out:
            raise RuntimeError("sfile HTTP/2 tidak produce output")
        return json.loads(out.splitlines()[-1])

    result = await loop.run_in_executor(None, do_run)

    dl = result.get("download") or {}
    if not dl.get("success"):
        raise Exception(
            f"sfile HTTP/2 download gagal: {dl.get('error')} (status {dl.get('status')})"
        )

    filepath = dl.get("path") or out_file
    if not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        raise Exception("sfile HTTP/2 selesai tapi file hasil tidak ketemu/kosong")

    # nama asli dari Content-Disposition CDN (lebih rapi daripada slug URL)
    real_name = dl.get("filename")
    if real_name:
        real_path = os.path.join(work_dir, os.path.basename(real_name))
        if real_path != filepath:
            if os.path.exists(real_path):
                os.remove(real_path)
            os.replace(filepath, real_path)
            filepath = real_path

    await render_status(
        ctx, "Download", percent=100.0,
        processed=os.path.getsize(filepath), total=os.path.getsize(filepath),
    )
    return filepath
