from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import app

# ganti dengan link/nomor donasi kamu sendiri
DONATE_TEXT = (
    "☕ Dukung Pengembangan Bot Ini\n\n"
    "Kalau bot ini membantu, kamu bisa donasi developer @waaadezig lewat:\n"
    "Scan Qris , Dana, atau Transfer Bank hehe\n"
    "Setiap dukungan sangat berarti untuk biaya server & maintenance (ngopi juga) 🙏"
)

HELP_TEXT = (
    "📖 Panduan Penggunaan\n\n"
    "**/mirror URL**\n"
    "Download dari URL (video/direct-link) lalu upload ke GoFile.\n"
    "Contoh: `/mirror https://youtu.be/xxxxx`\n\n"
    "**Reply + /mirror**\n"
    "Reply ke pesan yang ada link atau media (foto/video/dokumen, "
    "termasuk hasil forward) dengan `/mirror` tanpa argumen apapun.\n\n"
    "**/start** — Tampilkan menu awal\n"
    "**/donate** — Dukung pengembangan bot"
)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Cara Mirror", callback_data="help")],
        [InlineKeyboardButton("📊 Status", callback_data="status_open")],
        [InlineKeyboardButton("☕ Donate", callback_data="donate")],
    ])


@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply(
        "🤖 **Mirror Bot Aktif**\n\n"
        "Kirim `/mirror link , /m link , atau reply ke pesan/media dengan `/mirror`. "
        "Ketuk tombol di bawah buat panduan lengkap.",
        reply_markup=main_menu_keyboard(),
    )


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply(HELP_TEXT)


@app.on_message(filters.command("donate"))
async def donate_cmd(client, message):
    await message.reply(DONATE_TEXT)


@app.on_callback_query(filters.regex("^help$"))
async def help_callback(client, callback_query):
    await callback_query.message.reply(HELP_TEXT)
    await callback_query.answer()


@app.on_callback_query(filters.regex("^donate$"))
async def donate_callback(client, callback_query):
    await callback_query.message.reply(DONATE_TEXT)
    await callback_query.answer()
