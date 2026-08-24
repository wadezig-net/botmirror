import os
import re
import time
import uuid
import shutil
import asyncio

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import filters

from utils import is_premium
from config import app, DOWNLOAD_DIR, task_registry, task_history, pending_upload
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram
from uploader.gofile import upload_to_gofile
from uploader.history import add_entry

URL_RE = re.compile(r"https?://\S+")


def extract_url(message):
    if len(message.command) >= 2:
        return message.command[1]

    replied = message.reply_to_message
    if replied:
        text = replied.text or replied.caption or ""
        match = URL_RE.search(text)
        if match:
            return match.group(0)

    return None


@app.on_message(filters.command(["m", "mirror"]))
async def mirror(client, message):

    print(message.from_user.id)
    
    if not is_premium(message.from_user.id):
        username = (
        
        f"@{message.from_user.username}"
            if message.from_user.username
            else "Tidak ada username"
    )

        return await message.reply(
            f"❌ Maaf ya Anda belum memiliki akses premium.\n\n"
            f"👤 Username: {username}\n"
            f"🆔 User ID: `{message.from_user.id}`\n\n"
            f"Silahkan hubungi owner @waaadezig untuk aktivasi."
    )

    url = None

    replied = message.reply_to_message

    has_media = (
        replied
        and (
            replied.photo
            or replied.video
            or replied.document
            or replied.audio
            or replied.voice
            or replied.video_note
            or replied.sticker
        )
    )

    if not url and not has_media:
        await message.reply(
            "❌ Ngga ada yang bisa di-mirror\n\n"
            "Gunakan: /mirror URL\n"
            "atau reply ke pesan/media/forward yang ada"
        )
        return

    request_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(DOWNLOAD_DIR, request_id)
    os.makedirs(work_dir, exist_ok=True)

    upload_keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "☁️ GoFile",
                callback_data=f"upload_gofile:{request_id}"
            ),
            InlineKeyboardButton(
                "☁️ Google Drive",
                callback_data=f"upload_gdrive:{request_id}"
            ),
        ]
    ]
)

    status_msg = await message.reply(
    "📤 Pilih tujuan upload:",
    reply_markup=upload_keyboard
)

    ctx = {
        "status": status_msg,
        "last_text": [""],
        "request_id": request_id,
        "user_id": message.from_user.id if message.from_user else 0,
        "user_mention": message.from_user.mention() if message.from_user else "Unknown",
        "start_time": time.monotonic(),
        "title": url if url else "Media dari Telegram",
        "process": None,
        "phase": "Menyiapkan",
        "percent": 0.0,
        "task": asyncio.current_task(),
    }

    task_registry[request_id] = ctx

    pending_upload[request_id] = {
        "url": url,
        "replied": replied,
        "work_dir": work_dir,
        "ctx": ctx,
        "message": message,
    }

    return
