from pyrogram import filters
from config import app
from utils import is_admin, load_users, save_users


@app.on_message(filters.command("addpremium"))
async def addpremium(client, message):

    if not is_admin(message.from_user.id):
        return await message.reply(
            "❌ Tidak punya akses"
        )


    if len(message.command) < 2:
        return await message.reply(
            "Gunakan:\n/addpremium USER_ID"
        )


    user_id = int(message.command[1])

    data = load_users()

    if user_id not in data["premium"]:
        data["premium"].append(user_id)

    save_users(data)


    await message.reply(
        f"✅ User `{user_id}` sekarang PREMIUM"
    )



@app.on_message(filters.command("delpremium"))
async def delpremium(client, message):

    if not is_admin(message.from_user.id):
        return


    user_id = int(message.command[1])

    data = load_users()


    if user_id in data["premium"]:
        data["premium"].remove(user_id)


    save_users(data)


    await message.reply(
        f"✅ User `{user_id}` dihapus dari premium"
    )



@app.on_message(filters.command("premiumlist"))
async def premiumlist(client,message):

    if not is_admin(message.from_user.id):
        return


    data = load_users()

    text = "⭐ Premium:\n\n"

    for uid in data["premium"]:
        text += f"- `{uid}`\n"


    await message.reply(text)
