import os
import re
import time
import uuid
import shutil
import asyncio
from urllib.parse import urlparse

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import is_premium
from config import app, DOWNLOAD_DIR, task_registry, task_history, pending_upload
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram
from downloader.fichier import get_fichier_direct_link, FICHIER_DOMAINS
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

    # 1fichier: khusus, cukup kasih direct link ke user -- TIDAK ikut
    # download+upload seperti sumber lain (link 1fichier gampang expired
    # kalau di-fetch ulang terpisah pakai requests biasa).
    if url:
        domain = urlparse(url).netloc.lower()
        if any(d in domain for d in FICHIER_DOMAINS):
            fichier_request_id = uuid.uuid4().hex[:8]
            fichier_work_dir = os.path.join(DOWNLOAD_DIR, fichier_request_id)
            os.makedirs(fichier_work_dir, exist_ok=True)

            status_msg = await message.reply("🌐 Mencari direct link 1fichier...")

            fichier_ctx = {
                "status": status_msg,
                "last_text": [""],
                "request_id": fichier_request_id,
                "user_id": message.from_user.id if message.from_user else 0,
                "user_mention": message.from_user.mention() if message.from_user else "Unknown",
                "start_time": time.monotonic(),
                "title": url,
                "process": None,
                "phase": "Menyiapkan",
                "percent": 0.0,
                "task": asyncio.current_task(),
            }
            task_registry[fichier_request_id] = fichier_ctx

            try:
                result = await get_fichier_direct_link(url, fichier_work_dir, fichier_ctx)
                await status_msg.delete()
                await message.reply(
                    "✅ Direct link ditemukan\n\n"
                    f"📁 File:\n{result.get('filename', 'unknown')}\n\n"
                    f"🔗 Link:\n{result.get('direct_url')}\n\n"
                    "⚠️ Link ini kadang cuma valid sebentar/sekali pakai, "
                    "segera download manual."
                )
            except Exception as e:
                try:
                    await status_msg.edit(f"❌ Gagal:\n{e}")
                except Exception:
                    pass
            finally:
                task_registry.pop(fichier_request_id, None)
                shutil.rmtree(fichier_work_dir, ignore_errors=True)
            return

    request_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(DOWNLOAD_DIR, request_id)
    os.makedirs(work_dir, exist_ok=True)

    rename_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Rename File", callback_data=f"rename_yes:{request_id}")],
        [InlineKeyboardButton("⏩ Pakai Nama Asli", callback_data=f"rename_no:{request_id}")],
    ])

    upload_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ GoFile", callback_data=f"upload_gofile:{request_id}")],
        [InlineKeyboardButton("📁 Google Drive", callback_data=f"upload_gdrive:{request_id}")],
        [InlineKeyboardButton("📦 Zip & Upload", callback_data=f"upload_zip:{request_id}")],
    ])

    status_msg = await message.reply(
        "📁 Pilih nama file:",
        reply_markup=rename_keyboard,
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
        "filename": None,
        "upload_keyboard": upload_keyboard,
    }
