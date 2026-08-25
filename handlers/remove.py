from pyrogram import filters

from config import app
from uploader.gofile import delete_from_gofile
from uploader.gdrive import delete_from_gdrive
from uploader.history import get_entry, remove_entry, list_by_user


@app.on_message(filters.command(["rm"]))
async def remove_file(client, message):
    user_id = message.from_user.id if message.from_user else 0

    if len(message.command) < 2:
        entries = list_by_user(user_id)
        if not entries:
            await message.reply(
                "📭 Belum ada file yang bisa dihapus.\n\n"
                "Catatan: file cuma bisa dihapus selama bot belum di-restart sejak upload dilakukan."
            )
            return

        lines = ["🗑 File yang bisa dihapus:\n"]
        for short_id, entry in entries.items():
            provider = entry.get("provider", "gofile")
            lines.append(f"`{short_id}` — {entry.get('name', 'unknown')} ({provider})")
        lines.append("\nHapus dengan: `/rm <id>`")
        await message.reply("\n".join(lines))
        return

    short_id = message.command[1]
    entry = get_entry(short_id)

    if not entry:
        await message.reply("❌ ID tidak ditemukan. Cek daftar dengan `/rm` tanpa argumen.")
        return

    if entry.get("user_id") != user_id:
        await message.reply("❌ Kamu tidak bisa menghapus file milik user lain.")
        return

    status = await message.reply("⏳ Menghapus file...")
    provider = entry.get("type", "gofile")

    try:
        if provider == "gdrive":
            delete_from_gdrive(entry.get("file_id"))
        else:
            delete_from_gofile(entry.get("file_id"), entry.get("guest_token"))
        remove_entry(short_id)
        await status.edit(f"✅ File dihapus\n\n📁 {entry.get('name', 'unknown')}")
    except Exception as e:
        await status.edit(
            f"❌ Gagal hapus file:\n{e}\n\n"
            "Kemungkinan file sudah expired, atau bot sudah di-restart sejak upload."
        )
