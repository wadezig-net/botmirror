import os
import time
import asyncio
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

from status_ui import render_status
from utils import format_duration


async def upload_to_gofile(downloaded_file, ctx):
    """Upload file ke GoFile dengan progress bar + speed, lalu return data hasil upload."""
    total_size = os.path.getsize(downloaded_file)

    await render_status(ctx, "Upload", percent=0.0, processed=0, total=total_size)

    loop = asyncio.get_running_loop()
    upload_start = time.monotonic()
    last_update = [0.0]

    def on_upload_progress(monitor):
        now = time.monotonic()
        if now - last_update[0] < 2.5 and monitor.bytes_read < monitor.len:
            return
        last_update[0] = now

        elapsed = max(now - upload_start, 0.001)
        speed = monitor.bytes_read / elapsed
        percent = monitor.bytes_read / monitor.len * 100 if monitor.len else 0
        remaining = max(monitor.len - monitor.bytes_read, 0)
        eta = format_duration(remaining / speed) if speed > 0 else None

        asyncio.run_coroutine_threadsafe(
            render_status(
                ctx, "Upload", percent=percent,
                processed=monitor.bytes_read, total=total_size,
                speed=speed, eta=eta,
            ),
            loop,
        )

    def do_upload():
        with open(downloaded_file, "rb") as f:
            encoder = MultipartEncoder(fields={"file": (os.path.basename(downloaded_file), f)})
            monitor = MultipartEncoderMonitor(encoder, on_upload_progress)
            return requests.post(
                "https://upload.gofile.io/uploadfile",
                data=monitor,
                headers={"Content-Type": monitor.content_type},
                timeout=600,
            )

    upload = await asyncio.to_thread(do_upload)
    result = upload.json()

    if result.get("status") != "ok":
        raise Exception(f"Upload GoFile gagal: {result}")

    data = result["data"]
    return {
        "link": data.get("downloadPage"),
        "file_id": data.get("id") or data.get("fileId"),
        "guest_token": data.get("guestToken"),
    }


def delete_from_gofile(file_id, token):
    """Hapus file dari GoFile pakai contentId + token (guestToken atau akun)."""
    if not file_id or not token:
        raise Exception("Data file_id/token tidak lengkap, kemungkinan upload lama sebelum fitur ini ada.")

    resp = requests.delete(
        "https://api.gofile.io/contents",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"contentsId": file_id},
        timeout=30,
    )

    try:
        result = resp.json()
    except ValueError:
        raise Exception(f"Respon tidak valid dari GoFile (HTTP {resp.status_code}): {resp.text[:200]}")

    if result.get("status") != "ok":
        raise Exception(f"GoFile menolak hapus: {result}")

    return True
