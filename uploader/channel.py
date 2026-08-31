import os
import io

from config import app, TELEGRAM_CHANNEL_ID
from status_ui import render_status


def _channel_id():
    return int(str(TELEGRAM_CHANNEL_ID).replace("-100", ""))


async def upload_to_channel(downloaded_file, ctx):
    total_size = os.path.getsize(downloaded_file)

    await render_status(ctx, "Upload", percent=0.0, processed=0, total=total_size)

    filename = os.path.basename(downloaded_file)

    with open(downloaded_file, "rb") as f:
        file_data = io.BytesIO(f.read())

    await render_status(
        ctx, "📤 Mengirim ke Channel", percent=50.0,
        processed=total_size // 2, total=total_size,
        speed=0, eta=None,
    )

    await app.get_chat(_channel_id())

    try:
        sent = await app.send_document(
            chat_id=_channel_id(),
            document=file_data,
            file_name=filename,
            caption=f"⬇️ {filename}",
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