import time

from status_ui import render_status
from utils import format_duration


async def download_from_telegram(replied, work_dir, ctx):
    """Download media (foto/video/dokumen/dll) langsung dari pesan Telegram (termasuk hasil forward)."""
    start_time = time.monotonic()
    last_update = [0.0]

    async def progress(current, total):
        now = time.monotonic()
        if now - last_update[0] < 2.5 and current < total:
            return
        last_update[0] = now

        elapsed = max(now - start_time, 0.001)
        speed = current / elapsed
        percent = current / total * 100 if total else 0
        remaining = max(total - current, 0)
        eta = format_duration(remaining / speed) if speed > 0 else None
        await render_status(
            ctx, "Download", percent=percent,
            processed=current, total=total, speed=speed, eta=eta,
        )

    return await replied.download(file_name=f"{work_dir}/", progress=progress)
