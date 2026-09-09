import os
import time

from config import app, TELEGRAM_CHANNEL_ID
from status_ui import render_status


def _channel_id():
    return int(str(TELEGRAM_CHANNEL_ID).replace("-100", ""))


async def upload_to_channel(downloaded_file, ctx):
    total_size = os.path.getsize(downloaded_file)

    await render_status(ctx, "Upload", percent=0.0, processed=0, total=total_size)

    filename = os.path.basename(downloaded_file)

    chat_id = None

    # Enumerasi dialog men-cache semua peer yang bisa diakses bot (termasuk
    # channel target) dengan access_hash yang valid -- ini "mengenalkan" peer
    # ke sesi Pyrogram sehingga kirim via ID mentah tidak kena PEER_ID_INVALID.
    # Bot pernah kirim ke channel ini, jadi dijamin muncul di daftar dialog.
    try:
        async for dialog in app.get_dialogs():
            c = dialog.chat
            if c and c.id == TELEGRAM_CHANNEL_ID:
                chat_id = c.id
                break
    except Exception:
        pass

    if chat_id is None:
        # fallback: coba resolve langsung, kalau kebetulan peer-nya sudah
        # tercache di sesi dari kiriman sebelumnya
        for candidate in (_channel_id(), int(str(TELEGRAM_CHANNEL_ID))):
            try:
                await app.get_chat(candidate)
                chat_id = candidate
                break
            except Exception:
                continue

    if chat_id is None:
        raise Exception(
            f"Gagal resolve channel {TELEGRAM_CHANNEL_ID}. "
            f"Pastikan bot sudah di-add sebagai admin di channel ini, "
            f"dan channel-nya sudah pernah ada aktivitas apapun sejak bot ditambahkan."
        )

    start = time.monotonic()
    last_update = [0.0]

    async def progress(current, total):
        now = time.monotonic()
        # throttle biar nggak spam edit -> FLOOD_WAIT
        if now - last_update[0] < 1.0 and current < total:
            return
        last_update[0] = now
        elapsed = max(now - start, 0.001)
        speed = current / elapsed
        percent = (current / total * 100) if total else 0
        await render_status(
            ctx, "📤 Mengirim ke Channel", percent=percent,
            processed=current, total=total,
            speed=speed,
        )

    try:
        sent = await app.send_document(
            chat_id=chat_id,
            document=downloaded_file,  # kirim path file, bukan baca ke RAM
            file_name=filename,
            caption=f"⬇️ {filename}",
            progress=progress,
        )
    except Exception as e:
        raise Exception(f"Gagal kirim ke channel: {e}")

    link = getattr(sent, "link", None) or (
        f"https://t.me/c/{_channel_id()}/{sent.id}"
        if hasattr(sent, "id") else None
    )

    await render_status(
        ctx, "Upload", percent=100.0,
        processed=total_size, total=total_size,
    )

    return {
        "link": link,
        "file_id": str(sent.id),
        "guest_token": None,
    }
