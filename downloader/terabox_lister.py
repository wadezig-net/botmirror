import os
import json
import asyncio
import subprocess
from urllib.parse import urlparse

from config import TERABOX_SCRIPT, NODE_BIN, DOWNLOAD_DIR
from status_ui import render_status


async def terabox_list_files(url, work_dir, ctx):
    """
    Traverse folder Terabox, dapat daftar semua file, tanpa download.
    Return dict berisi daftar file + metadata, atau raise exception kalau gagal.
    """
    await render_status(ctx, "🌐 Membuka halaman Terabox & traverse folder...")

    if not os.path.isfile(TERABOX_SCRIPT):
        raise Exception(f"Script {TERABOX_SCRIPT} tidak ditemukan.")

    if not os.path.isfile(NODE_BIN):
        raise Exception(f"Node binary tidak ditemukan di {NODE_BIN}.")

    # Jalankan script JS
    proc = await asyncio.create_subprocess_exec(
        NODE_BIN,
        TERABOX_SCRIPT,
        url,
        work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise Exception("Timeout traverse folder Terabox (>180s)")

    output = stdout.decode(errors="ignore").strip()
    try:
        result = json.loads(output.splitlines()[-1])
    except Exception:
        raise Exception(f"Gagal parse output Terabox: {output[:500]}")

    if not result.get("ok"):
        raise Exception(f"Terabox traverse gagal: {result.get('error')}")

    # Build response untuk handler
    return {
        "title": result.get("title", "Folder Terabox"),
        "totalFolders": result.get("totalFolders", 0),
        "totalFiles": result.get("totalFiles", 0),
        "file_list": result.get("file_list", []),
        "text_for_telegram": result.get("text_for_telegram", ""),
        "json_for_telegram": result.get("json_for_telegram", ""),
        "cookie_file": result.get("cookie_file", ""),
        "referer": result.get("referer", ""),
    }
