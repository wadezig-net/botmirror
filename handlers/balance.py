import os

from pyrogram import filters

from config import app
from utils import (
    is_owner, get_balance, add_balance, get_daily_usage,
    FREE_QUOTA_PER_DAY, FREE_MAX_FILE_GB,
)

QRIS_FILE = "database/qris.png"

TOPUP_TEXT = (
    "💎 **Topup Saldo**\n\n"
    "Saldo dipakai buat mirror file yang lewat kuota gratis "
    f"({FREE_QUOTA_PER_DAY}x/hari, maks {FREE_MAX_FILE_GB}GB per file).\n\n"
    "Harga: **20.000 💎/bulan**\n\n"
    "Cara topup:\n"
    "1. Scan QRIS / transfer ke admin (lihat /donate).\n"
    "2. Kirim bukti transfer + user ID kamu ke @waaadezig.\n\n"
    "Setelah saldo masuk, mirror unlimited, gasss"
)


def qris_available():
    return os.path.isfile(QRIS_FILE)


@app.on_message(filters.command(["saldo", "balance", "bal"]))
async def saldo_cmd(client, message):
    uid = message.from_user.id

    usage = get_daily_usage(uid)
    bal = get_balance(uid)

    await message.reply(
        f"👤 **Saldo & Kuota Kamu**\n\n"
        f"💎 Saldo: **{bal:.2f}**\n"
        f"📥 Mirror gratis hari ini: **{usage['count']}/{FREE_QUOTA_PER_DAY}**\n"
        f"📊 Total gratis terpakai: {usage['bytes']/1024/1024:.2f} MB\n\n"
        "Topup saldo buat mirror unlimited → ketik /topup"
    )


@app.on_message(filters.command(["topup"]))
async def topup_cmd(client, message):
    if qris_available():
        await message.reply_photo(QRIS_FILE, caption=TOPUP_TEXT)
    else:
        await message.reply(TOPUP_TEXT)


@app.on_message(filters.command(["addbalance", "addbal"]))
async def addbalance(client, message):
    if not is_owner(message.from_user.id):
        return await message.reply("❌ Hanya owner yang punya akses")

    if len(message.command) < 2:
        return await message.reply(
            "⚠️ Cara pakai:\n`/addbalance USER_ID jumlah`\n"
            "atau reply ke pesan user: `/addbalance jumlah`"
        )

    try:
        amount = float(message.command[-1].replace(",", "."))
    except ValueError:
        return await message.reply("⚠️ Jumlah saldo harus angka (mis. `/addbalance 5`)")

    # user_id: pakai reply kalau ada, selain itu argumen kedua (bukan amount)
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        try:
            target_id = int(message.command[1].replace(",", ""))
        except ValueError:
            return await message.reply("⚠️ User ID tidak valid")

    new_bal = add_balance(target_id, amount)
    await message.reply(
        f"✅ Saldo `{target_id}` ditambah **{amount:.2f} 💎**.\n"
        f"Sisa saldo: **{new_bal:.2f} 💎**"
    )
