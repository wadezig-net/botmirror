from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import app
from utils import (
    is_owner, get_balance, get_daily_usage,
    FREE_QUOTA_PER_DAY, FREE_MAX_FILE_GB,
)

QRIS_FILE = "database/qris.png"

# ganti dengan link/nomor donasi kamu sendiri
QRIS_EXISTS_TEXT = (
    "☕ Dukung Pengembangan Bot Ini\n\n"
    "Scan QRIS di bawah buat donasi, atau bisa juga lewat Dana / Transfer Bank.\n"
    "Setiap dukungan sangat berarti untuk biaya server & maintenance (ngopi juga) 🙏"
)

DONATE_TEXT = (
    "☕ Dukung Pengembangan Bot Ini\n\n"
    "Belum ada QRIS aktif. Donasi bisa lewat Dana / Transfer Bank.\n"
    "Hubungi owner @waaadezig buat info nomor donasi.\n"
    "Setiap dukungan sangat berarti untuk biaya server & maintenance (ngopi juga) 🙏"
)


def qris_available():
    import os
    return os.path.isfile(QRIS_FILE)


@app.on_message(filters.command("donate"))
async def donate_cmd(client, message):
    if qris_available():
        await message.reply_photo(QRIS_FILE, caption=QRIS_EXISTS_TEXT)
    else:
        await message.reply(DONATE_TEXT)


@app.on_callback_query(filters.regex("^donate$"))
async def donate_callback(client, callback_query):
    if qris_available():
        await callback_query.message.reply_photo(QRIS_FILE, caption=QRIS_EXISTS_TEXT)
    else:
        await callback_query.message.reply(DONATE_TEXT)
    await callback_query.answer()


@app.on_message(filters.command(["setqris"]))
async def setqris(client, message):
    """Owner menyimpan/set QRIS. Reply gambar QRIS ke perintah ini (atau lampirkan)."""
    if not is_owner(message.from_user.id):
        return await message.reply("❌ Hanya owner yang punya akses")

    photo = None
    if message.photo:
        photo = message.photo
    elif message.reply_to_message:
        photo = message.reply_to_message.photo or getattr(message.reply_to_message, "document", None)

    if photo is None:
        return await message.reply(
            "⚠️ Kirim gambar QRIS bersamaan dengan `/setqris`\n"
            "atau reply foto QRIS lalu ketik `/setqris`."
        )

    import os
    os.makedirs("database", exist_ok=True)

    # download dari pesan yang benar-benar memuat fotonya
    src_msg = message if message.photo else message.reply_to_message
    await src_msg.download(file_name=QRIS_FILE)

    await message.reply("✅ QRIS donasi berhasil disimpan. Kini muncul di /donate")


@app.on_message(filters.command(["delqris"]))
async def delqris(client, message):
    if not is_owner(message.from_user.id):
        return await message.reply("❌ Hanya owner yang punya akses")

    import os
    if os.path.isfile(QRIS_FILE):
        os.remove(QRIS_FILE)
        return await message.reply("🗑️ QRIS donasi dihapus. /donate kembali ke mode teks.")

    return await message.reply("ℹ️ QRIS belum diset.")

HELP_TEXT = (
    "📖 Panduan Penggunaan\n\n"
    "**/mirror URL**\n"
    "Download dari URL (video/direct-link) lalu upload ke GoFile.\n"
    "Contoh: `/mirror https://youtu.be/xxxxx`\n\n"
    "**Reply + /mirror**\n"
    "Reply ke pesan yang ada link atau media (foto/video/dokumen, "
    "termasuk hasil forward) dengan `/mirror` tanpa argumen apapun.\n\n"
    "**/start** — Tampilkan menu awal\n"
    "**/saldo** — Cek saldo & kuota gratis kamu\n"
    "**/topup** — Info cara topup saldo (buat mirror unlimited)\n"
    "**/donate** — Dukung pengembangan bot"
)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Cara Mirror", callback_data="help")],
        [InlineKeyboardButton("💎 Saldo & Topup", callback_data="saldo_info")],
        [InlineKeyboardButton("📊 Status", callback_data="status_open")],
        [InlineKeyboardButton("☕ Donate", callback_data="donate")],
    ])


INVISIBLE = "\u2060"  # word joiner, tampil sebagai teks kosong (~hanya tombol)


@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply(
        INVISIBLE,
        reply_markup=main_menu_keyboard(),
    )


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply(HELP_TEXT)


@app.on_callback_query(filters.regex("^help$"))
async def help_callback(client, callback_query):
    await callback_query.message.reply(HELP_TEXT)
    await callback_query.answer()


@app.on_callback_query(filters.regex("^saldo_info$"))
async def saldo_info_callback(client, callback_query):
    uid = callback_query.from_user.id
    usage = get_daily_usage(uid)
    bal = get_balance(uid)

    text = (
        "💎 **Saldo & Topup**\n\n"
        f"💎 Saldo kamu: **{bal:.2f}**\n"
        f"📥 Kuota gratis hari ini: **{usage['count']}/{FREE_QUOTA_PER_DAY}** "
        f"(maks {FREE_MAX_FILE_GB}GB/file)\n\n"
        "Harga premium: **20.000 💎/bulan**\n"
        "Kalau kuota gratis habis atau mau mirror file besar, "
        "pakai saldo buat mirror unlimited.\n\n"
        "**Cara topup:** scan QRIS/transfer ke owner (lihat /donate), "
        "kirim bukti + user ID ke @waaadezig."
    )
    await callback_query.message.reply(text)
    await callback_query.answer()
