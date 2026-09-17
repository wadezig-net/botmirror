from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def build_rename_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Rename File", callback_data=f"rename_yes:{request_id}")],
        [InlineKeyboardButton("⏩ Pakai Nama Asli", callback_data=f"rename_no:{request_id}")],
        [InlineKeyboardButton("◀️ Kembali", callback_data=f"rename_back:{request_id}")],
    ])


def build_upload_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ GoFile", callback_data=f"upload_gofile:{request_id}")],
        [InlineKeyboardButton("📁 Google Drive", callback_data=f"upload_gdrive:{request_id}")],
        [InlineKeyboardButton("📨 Telegram Channel", callback_data=f"upload_channel:{request_id}")],
        [InlineKeyboardButton("📦 Zip & Upload", callback_data=f"upload_zip:{request_id}")],
        [InlineKeyboardButton("◀️ Kembali", callback_data=f"upload_back:{request_id}")],
    ])


def build_resolution_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("360p", callback_data=f"yt_res:{request_id}:360"),
         InlineKeyboardButton("720p", callback_data=f"yt_res:{request_id}:720")],
        [InlineKeyboardButton("1080p", callback_data=f"yt_res:{request_id}:1080"),
         InlineKeyboardButton("📺 Max", callback_data=f"yt_res:{request_id}:max")],
        [InlineKeyboardButton("◀️ Kembali", callback_data=f"mirror_cancel:{request_id}")],
    ])


def build_cancel_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Batal", callback_data=f"mirror_cancel:{request_id}")],
    ])