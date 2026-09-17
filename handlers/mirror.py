import os
import re
import time
import uuid
import shutil
import asyncio
from urllib.parse import urlparse

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import (
    is_owner, is_premium, can_start_task, probe_url_size, owner_prompt,
    FREE_MAX_FILE_BYTES, FREE_MAX_FILE_GB,
    can_use_free, get_daily_usage, can_pay_with_balance, cost_in_points,
    FREE_QUOTA_PER_DAY,
)
from config import app, DOWNLOAD_DIR, task_registry, task_history, pending_upload
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram
from downloader.fichier import get_fichier_direct_link, FICHIER_DOMAINS
from downloader.terabox_lister import terabox_list_files, TERABOX_DOMAINS
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


def is_youtube_link(url):
    if not url:
        return False
    domain = urlparse(url).netloc.lower()
    return "youtu.be" in domain or "youtube.com" in domain


def build_rename_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Rename File", callback_data=f"rename_yes:{request_id}")],
        [InlineKeyboardButton("⏩ Pakai Nama Asli", callback_data=f"rename_no:{request_id}")],
    ])


def _media_size(replied):
    """Ambil ukuran file (bytes) dari pesan media Telegram, kalau ada."""
    if not replied:
        return None
    for attr in ("document", "video", "audio", "video_note", "animation",
                 "voice", "photo", "sticker"):
        obj = getattr(replied, attr, None)
        if obj is not None:
            if hasattr(obj, "file_size"):
                return obj.file_size
            # photo pakai .file_size di photo objek; fallback ke .file_size pesan
    try:
        if getattr(replied, "file_size", None):
            return replied.file_size
    except Exception:
        pass
    return None


def _check_early_access(user_id, file_size_bytes):
    """Cek akses di awal. Return (boleh: bool, alasan: str|None, pesan_topup: str|None)."""
    if is_owner(user_id) or is_premium(user_id):
        return True, None, None

    if file_size_bytes and file_size_bytes > FREE_MAX_FILE_BYTES:
        cost = cost_in_points(file_size_bytes)
        if not can_pay_with_balance(user_id, file_size_bytes):
            return (
                False,
                None,
                f"❌ File ini {file_size_bytes/1024**3:.2f} GB, lewat batas gratis "
                f"({FREE_MAX_FILE_GB} GB).\n\nButuh saldo {cost:.2f} 💎 buat mirror file ini. "
                f"{owner_prompt()}",
            )
        # saldo cukup -> lanjut; potong di upload_select
        return True, None, None

    ok, reason = can_use_free(user_id, file_size_bytes)
    if not ok:
        # kuota gratis habis -> cek saldo
        cost = cost_in_points(file_size_bytes or 0)
        if can_pay_with_balance(user_id, file_size_bytes):
            return True, None, None
        return (
            False,
            None,
            f"Hari ini kamu udah pakai "
            f"{get_daily_usage(user_id)['count']}/{FREE_QUOTA_PER_DAY} mirror gratis.\n"
            f"Topup saldo 20k/bulan biar bisa akses premium mirror lagi. "
            f"Gunakan /topup dan hub @waaadezig untuk aktivasi premium.\n"
            f"❤️happy mirror❤️",
        )

    return True, None, None


@app.on_message(filters.command(["m", "mirror"]))
async def mirror(client, message):
    print(message.from_user.id)

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
    #
    # Terabox: traverse dulu, tampilkan daftar file, beri opsi lanjut atau batal.
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

    _ok, _limit_msg = can_start_task(message.from_user.id)
    if not _ok:
        return await message.reply(_limit_msg)

    # ---- Terabox: tampilkan daftar file sebelum lanjut ----
    if url and any(d in domain for d in TERABOX_DOMAINS):
        await _handle_terabox_preview(message, url)
        return  # <-- STOP di sini, tunggu callback user

    # ---- Deteksi ukuran awal (early check) ----
    early_size = _media_size(replied)
    if early_size is None and url:
        try:
            early_size = await asyncio.to_thread(probe_url_size, url)
        except Exception:
            early_size = None

    if not (is_owner(message.from_user.id) or is_premium(message.from_user.id)):
        _e_ok, _e_reason, _e_prompt = _check_early_access(message.from_user.id, early_size)
        if not _e_ok:
            return await message.reply(_e_prompt or _e_reason or "❌ Tidak bisa mirror.")

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
        [InlineKeyboardButton("📨 Telegram Channel", callback_data=f"upload_channel:{request_id}")],
        [InlineKeyboardButton("📦 Zip & Upload", callback_data=f"upload_zip:{request_id}")],
    ])

    if is_youtube_link(url):
        # YouTube: tanya resolusi dulu sebelum lanjut rename/upload.
        yt_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("360p", callback_data=f"yt_res:{request_id}:360"),
             InlineKeyboardButton("720p", callback_data=f"yt_res:{request_id}:720")],
            [InlineKeyboardButton("1080p", callback_data=f"yt_res:{request_id}:1080"),
             InlineKeyboardButton("📺 Max", callback_data=f"yt_res:{request_id}:max")],
        ])
        status_msg = await message.reply(
            "🎬 **Pilih resolusi download:**\n\n"
            "YouTube sekarang streaming dengan eksperimen SABR — tanpa pilihan "
            "resolusi, bot biasanya cuma bisa dapat 360p. Pilih sesuai kebutuhan:",
            reply_markup=yt_keyboard,
        )
    else:
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
        "early_size": early_size,
        "upload_keyboard": upload_keyboard,
    }


async def _handle_terabox_preview(message, url):
    """
    Untuk link Terabox, tampilkan daftar file hasil traverse,
    beri user opsi Lanjut / Batal.
    """
    request_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(DOWNLOAD_DIR, request_id)
    os.makedirs(work_dir, exist_ok=True)

    status_msg = await message.reply("🌐 Membuka halaman Terabox & traverse folder...")

    ctx = {
        "status": status_msg,
        "last_text": [""],
        "request_id": request_id,
        "user_id": message.from_user.id if message.from_user else 0,
        "user_mention": message.from_user.mention() if message.from_user else "Unknown",
        "start_time": time.monotonic(),
        "title": url,
        "process": None,
        "phase": "Membuka halaman",
        "percent": 0.0,
        "task": asyncio.current_task(),
    }
    task_registry[request_id] = ctx

    try:
        result = await terabox_list_files(url, work_dir, ctx)

        # Hapus pesan status, tampilkan daftar file
        try:
            await ctx["status"].delete()
        except Exception:
            pass

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Lanjut Mirror", callback_data=f"terabox_go:{request_id}")],
            [InlineKeyboardButton("❌ Batal", callback_data=f"terabox_cancel:{request_id}")],
        ])

        # Kirim summary + dokumen daftar file
        title = result.get("title", "Folder Terabox")
        file_count = result.get("totalFiles", 0)
        folder_count = result.get("totalFolders", 0)
        summary = (
            f"📁 **{title}**\n\n"
            f"📂 Folder: `{folder_count}`\n"
            f"📄 File: `{file_count}`\n\n"
            f"💡 Klik **Lanjut Mirror** kalau mau mirror file ini (terima apa adanya).\n"
            f"💡 Atau **Batal** kalau nggak mau."
        )

        await message.reply(summary, reply_markup=keyboard)

        # Kirim file list sebagai dokumen (teks)
        file_list_path = os.path.join(work_dir, "file_list_text.txt")
        if os.path.isfile(file_list_path):
            try:
                await message.reply_document(
                    document=file_list_path,
                    filename="terabox_file_list.txt",
                    caption=f"Daftar {file_count} file dari {title}",
                )
            except Exception:
                pass  # Kalau gagal kirim dokumen, tidak gentle

        # Simpan result di pending_upload supaya bisa diakses oleh callback handler
        pending_upload[request_id] = {
            "url": url,
            "replied": None,
            "work_dir": work_dir,
            "ctx": ctx,
            "message": message,
            "filename": None,
            "early_size": None,
            "upload_keyboard": None,
            "terabox_result": result,
            "terabox_mode": True,
        }

    except Exception as e:
        try:
            await ctx["status"].edit(f"❌ Gagal traverse Terabox:\\n{e}")
        except Exception:
            pass
        task_registry.pop(request_id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        # Kalau gagal, tetap lanjut ke flow normal (download)
        return False

    return True


# ---- Callback: Terabox preview (Lanjut / Batal) ----
@app.on_callback_query(filters.regex(r"^terabox_(go|cancel):"))
async def terabox_callback(client, callback_query):
    """
    Menangani klik tombol preview Terabox:
    - terabox_go   : lanjut ke download (biasanya via ytdlp router).
    - terabox_cancel : hapus konteks & pekerjaan sementara.
    """
    raw = callback_query.data  # e.g. "terabox_go:abc12345"
    prefix, request_id = raw.split(":", 1)
    action = prefix.replace("terabox_", "")

    task = pending_upload.get(request_id)
    if not task:
        return await callback_query.answer("Session sudah habis.", show_alert=True)

    await callback_query.answer()

    if action == "cancel":
        await callback_query.message.delete()
        task_registry.pop(request_id, None)
        shutil.rmtree(task.get("work_dir"), ignore_errors=True)
        pending_upload.pop(request_id, None)
        return

    # ---- Lanjut mirror ----
    # Hapus pesan preview.
    try:
        await callback_query.message.delete()
    except Exception:
        pass

    url = task.get("url")
    user_id = task["ctx"]["user_id"]
    user_mention = task["ctx"]["user_mention"]
    work_dir = task.get("work_dir")

    # Buang konteks preview; buat konteks baru untuk download.
    task_registry.pop(request_id, None)

    _ok, _limit_msg = can_start_task(user_id)
    if not _ok:
        await callback_query.message.reply(_limit_msg)
        shutil.rmtree(work_dir, ignore_errors=True)
        pending_upload.pop(request_id, None)
        return

    new_request_id = uuid.uuid4().hex[:8]
    new_work_dir = os.path.join(DOWNLOAD_DIR, new_request_id)
    os.makedirs(new_work_dir, exist_ok=True)

    status_msg = await callback_query.message.reply("📥 Mendownload dari Terabox...")

    new_ctx = {
        "status": status_msg,
        "last_text": [""],
        "request_id": new_request_id,
        "user_id": user_id,
        "user_mention": user_mention,
        "start_time": time.monotonic(),
        "title": url,
        "process": None,
        "phase": "Mendownload",
        "percent": 0.0,
        "task": asyncio.current_task(),
    }
    task_registry[new_request_id] = new_ctx

    try:
        downloaded = await download_via_url(url, new_work_dir, new_ctx)
    except Exception as e:
        try:
            await status_msg.edit(f"❌ Download gagal:\\n{e}")
        except Exception:
            pass
        task_registry.pop(new_request_id, None)
        shutil.rmtree(new_work_dir, ignore_errors=True)
        pending_upload.pop(request_id, None)
        return

    # Siapkan upload — skip rename, langsung ke pilihan upload.
    try:
        await status_msg.delete()
    except Exception:
        pass

    upload_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ GoFile", callback_data=f"upload_gofile:{new_request_id}")],
        [InlineKeyboardButton("📁 Google Drive", callback_data=f"upload_gdrive:{new_request_id}")],
        [InlineKeyboardButton("📨 Telegram Channel", callback_data=f"upload_channel:{new_request_id}")],
        [InlineKeyboardButton("📦 Zip & Upload", callback_data=f"upload_zip:{new_request_id}")],
    ])

    await callback_query.message.reply(
        f"✅ Download selesai: `{os.path.basename(downloaded)}`\\n\\nPilih tujuan upload:",
        reply_markup=upload_keyboard,
    )

    pending_upload[new_request_id] = {
        "url": url,
        "replied": None,
        "work_dir": new_work_dir,
        "ctx": new_ctx,
        "message": callback_query.message,
        "filename": None,
        "early_size": None,
        "upload_keyboard": upload_keyboard,
    }

    # Hapus pending Terabox lama.
    pending_upload.pop(request_id, None)
    shutil.rmtree(work_dir, ignore_errors=True)


# ---- Callback: pilihan resolusi YouTube (360/720/1080/max) ----
YT_FORMATS = {
    "360": "bestvideo[height<=360]+bestaudio/best",
    "720": "bestvideo[height<=720]+bestaudio/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best",
    "max": "bestvideo+bestaudio/best",
}


@app.on_callback_query(filters.regex(r"^yt_res:"))
async def yt_resolution_callback(client, callback_query):
    """
    Menangani pilihan resolusi YouTube dari keyboard:
    - simpan pilihan di ctx["yt_format"] (dipakai download_via_url)
    - lanjut ke prompt rename (alias nama file) seperti flow normal.
    """
    try:
        _prefix, request_id, res = callback_query.data.split(":", 2)
    except ValueError:
        return await callback_query.answer("Data tombol rusak.", show_alert=True)

    task = pending_upload.get(request_id)
    if not task:
        return await callback_query.answer("❌ Task sudah expired", show_alert=True)

    await callback_query.answer()

    fmt = YT_FORMATS.get(res, "bestvideo[height<=1080]+bestaudio/best")
    task["ctx"]["yt_format"] = fmt
    task["yt_format"] = fmt

    label = "Maksimum" if res == "max" else f"{res}p"
    try:
        await callback_query.message.edit_text(
            f"🎬 Resolusi: **{label}** dipilih.\n\n"
            "📁 Lanjut — pilih nama file:",
            reply_markup=build_rename_keyboard(request_id),
        )
    except Exception:
        pass
