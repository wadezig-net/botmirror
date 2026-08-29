import os

from pyrogram import filters

from config import app
from utils import is_admin

BACKUP_PDF = "/root/botmirror-progres-backup.pdf"


@app.on_message(filters.command(["backup", "pdf"]))
async def backup(client, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Tidak punya akses (khusus owner/admin).")

    if not os.path.isfile(BACKUP_PDF):
        return await message.reply("❌ File backup tidak ditemukan di server.")

    try:
        await message.reply_document(
            BACKUP_PDF,
            caption="📄 Backup progres BotMirror (PDF)",
        )
    except Exception as e:
        await message.reply(f"❌ Gagal mengirim PDF:\n{e}")