import os
import re
import time
import uuid
import shutil
import asyncio

from pyrogram import filters

from config import app, DOWNLOAD_DIR, task_registry, task_history
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram
from uploader.gofile import upload_to_gofile

URL_RE = re.compile(r"https?://\S+")


def extract_url(message):
    """
    Ambil URL dari command argument (/mirror <url>), atau kalau tidak ada,
    coba cari di pesan yang di-reply (mendukung reply ke pesan biasa maupun
    hasil forward dari chat/channel lain -- captionnya tetap kebawa).
    """
    if len(message.command) >= 2:
        return message.command[1]

    replied = message.reply_to_message
    if replied:
        text = replied.text or replied.caption or ""
        match = URL_RE.search(text)
        if match:
            return match.group(0)

    return None


@app.on_message(filters.command(["mirror", "m"]))
async def mirror(client, message):
    url = extract_url(message)
    replied = message.reply_to_message
    has_media = bool(
        replied and (
            replied.photo or replied.video or replied.document or
            replied.audio or replied.animation or replied.voice or
            replied.video_note or replied.sticker
        )
    )

    if not url and not has_media:
        await message.reply(
            "❌ Nggak ada yang bisa di-mirror\n\n"
            "Gunakan: `/mirror URL`\n"
            "atau reply ke pesan/media/forward yang ada link atau file-nya dengan `/mirror`"
        )
        return

    # folder unik per-request supaya tidak bentrok kalau banyak user mirror bareng
    request_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(DOWNLOAD_DIR, request_id)
    os.makedirs(work_dir, exist_ok=True)

    status_msg = await message.reply("⏳ Menyiapkan download...")

    ctx = {
        "status": status_msg,
        "last_text": [""],
        "request_id": request_id,
        "user_id": message.from_user.id if message.from_user else 0,
        "user_mention": message.from_user.mention() if message.from_user else "Unknown",
        "start_time": time.monotonic(),
        "title": url if url else "Media dari Telegram",
        "process": None,        # subprocess aktif (buat di-kill kalau di-cancel)
        "phase": "Menyiapkan",  # buat ditampilin di /status
        "percent": 0.0,
        "task": asyncio.current_task(),
    }
    task_registry[request_id] = ctx

    downloaded_file = None
    final_state = "error"
    error_message = None

    try:
        if url:
            downloaded_file = await download_via_url(url, work_dir, ctx)
        else:
            downloaded_file = await download_from_telegram(replied, work_dir, ctx)

        ctx["title"] = os.path.basename(downloaded_file)
        link = await upload_to_gofile(downloaded_file, ctx)
        size_mb = os.path.getsize(downloaded_file) / 1024 / 1024

        # kirim hasil akhir sebagai PESAN BARU (bukan edit panel progress yang lama).
        # Ini penting: panel progress udah di-edit berkali-kali selama proses jalan,
        # jadi kalau kena flood-limit dari histori edit sebelumnya, edit terakhir buat
        # nampilin link bisa ikut gagal juga -- pesan baru nggak kebawa masalah itu.
        await message.reply(
            "✅ Mirror selesai\n\n"
            f"📁 File:\n{os.path.basename(downloaded_file)}\n\n"
            f"📦 Size: {size_mb:.2f} MB\n\n"
            f"🔗 Link:\n{link}"
        )
        final_state = "done"

    except asyncio.CancelledError:
        final_state = "cancelled"
        try:
            await message.reply("🚫 Dibatalkan oleh user")
        except Exception:
            pass

    except Exception as e:
        error_message = str(e)
        try:
            await message.reply(f"❌ Gagal:\n{error_message}")
        except Exception as reply_err:
            print(f"[mirror] gagal kirim pesan error: {reply_err}")

    finally:
        # panel progress udah nggak dibutuhin lagi setelah hasil akhir dikirim
        # sebagai pesan baru -- hapus biar chat nggak numpuk
        try:
            await status_msg.delete()
        except Exception as del_err:
            print(f"[mirror] gagal hapus panel progress: {del_err}")

        # bersihkan folder request ini (bukan seluruh DOWNLOAD_DIR)
        shutil.rmtree(work_dir, ignore_errors=True)
        task_registry.pop(request_id, None)
        task_history.appendleft({
            "title": ctx["title"],
            "user_mention": ctx["user_mention"],
            "state": final_state,
            "error": error_message,
            "finished_at": time.time(),
        })
