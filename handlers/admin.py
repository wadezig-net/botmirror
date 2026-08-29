from pyrogram import filters
from config import app
from utils import is_admin, load_users, save_users


async def _resolve_user(client, message):
    """Mendapatkan user_id dari argumen ID, @username, atau reply ke pesan user."""

    target = None

    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and not message.reply_to_message.from_user.is_bot
    ):
        target = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        raw = message.command[1].strip()

        try:
            return int(raw.replace(",", "").replace(" ", ""))
        except ValueError:
            pass

        if raw.startswith("@") or (not raw.isdigit()):
            try:
                target = await client.get_users(raw.lstrip("@"))
            except Exception:
                raise ValueError("Username tidak ditemukan")

    if target is None:
        raise ValueError("Target tidak jelas (butuh ID / @username / reply)")

    return target.id


@app.on_message(filters.command(["addpremium", "addprem"]))
async def addpremium(client, message):

    if not is_admin(message.from_user.id):
        return await message.reply("❌ Tidak punya akses")

    try:
        user_id = await _resolve_user(client, message)
    except ValueError as e:
        return await message.reply(f"⚠️ {e}\n\nGunakan:\n`/addpremium USER_ID`\n`/addpremium @username`\natau reply ke pesan user lalu ketik `/addpremium`")

    data = load_users()

    if user_id in data["admins"] or user_id in data["owner"]:
        return await message.reply(f"⚠️ `{user_id}` sudah admin/owner.")

    if user_id in data["premium"]:
        return await message.reply(f"ℹ️ User `{user_id}` sudah PREMIUM.")

    data["premium"].append(user_id)
    save_users(data)

    await message.reply(f"✅ User `{user_id}` sekarang PREMIUM. Total: `{len(data['premium'])}` premium")


@app.on_message(filters.command(["delpremium", "delprem"]))
async def delpremium(client, message):

    if not is_admin(message.from_user.id):
        return await message.reply("❌ Tidak punya akses")

    try:
        user_id = await _resolve_user(client, message)
    except ValueError as e:
        return await message.reply(f"⚠️ {e}\n\nGunakan:\n`/delpremium USER_ID`\n`/delpremium @username`\natau reply ke pesan user lalu ketik `/delpremium`")

    data = load_users()

    if user_id in data["premium"]:
        data["premium"].remove(user_id)
        save_users(data)
        return await message.reply(f"✅ User `{user_id}` dihapus dari premium. Total: `{len(data['premium'])}` premium")

    return await message.reply(f"ℹ️ User `{user_id}` tidak ada di daftar premium.")


@app.on_message(filters.command(["addadmin"]))
async def addadmin(client, message):

    if not is_admin(message.from_user.id):
        return await message.reply("❌ Tidak punya akses")

    try:
        user_id = await _resolve_user(client, message)
    except ValueError as e:
        return await message.reply(f"⚠️ {e}\n\nGunakan:\n`/addadmin USER_ID`\n`/addadmin @username`\natau reply ke pesan user lalu ketik `/addadmin`")

    data = load_users()

    if user_id in data["owner"]:
        return await message.reply(f"⚠️ `{user_id}` adalah owner.")

    if user_id in data["admins"]:
        return await message.reply(f"ℹ️ User `{user_id}` sudah admin.")

    data["admins"].append(user_id)
    save_users(data)

    await message.reply(f"✅ User `{user_id}` diangkat jadi admin.")


@app.on_message(filters.command(["deladmin"]))
async def deladmin(client, message):

    if not is_admin(message.from_user.id):
        return await message.reply("❌ Tidak punya akses")

    try:
        user_id = await _resolve_user(client, message)
    except ValueError as e:
        return await message.reply(f"⚠️ {e}\n\nGunakan:\n`/deladmin USER_ID`\n`/deladmin @username`\natau reply ke pesan user lalu ketik `/deladmin`")

    data = load_users()

    if user_id in data["admins"]:
        data["admins"].remove(user_id)
        save_users(data)
        return await message.reply(f"✅ User `{user_id}` dicabut dari admin.")

    return await message.reply(f"ℹ️ User `{user_id}` tidak ada di daftar admin.")


@app.on_message(filters.command(["premiumlist", "userlist"]))
async def premiumlist(client, message):

    if not is_admin(message.from_user.id):
        return

    data = load_users()

    text = "⭐ **Premium**\n\n"

    if data["premium"]:
        for uid in data["premium"]:
            text += f"- `{uid}`\n"
    else:
        text += "_Belum ada._\n"

    text += "\n👑 **Owner**\n"
    for uid in data["owner"]:
        text += f"- `{uid}`\n"

    if data["admins"]:
        text += "\n🛡️ **Admin**\n"
        for uid in data["admins"]:
            text += f"- `{uid}`\n"

    await message.reply(text)