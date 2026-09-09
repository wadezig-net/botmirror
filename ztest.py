import asyncio
import os
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

kb = InlineKeyboardMarkup([
    [InlineKeyboardButton("X", callback_data="x")],
])

CANDIDATES = [
    ("\u200b", "ZWSP"),
    ("\u200c", "ZWNJ"),
    ("\u200d", "ZWJ"),
    ("\u00a0", "NBSP"),
    ("\u202f", "NNBSP"),
    (" ", "space"),
    ("\u2060", "WJ"),
]


async def main():
    app = Client(
        "ztest",
        api_id=int(os.getenv("API_ID")),
        api_hash=os.getenv("API_HASH"),
        bot_token=os.getenv("BOT_TOKEN"),
    )
    await app.start()
    chat = int(os.getenv("OWNER_ID", "440559453"))
    print("connected", flush=True)
    for txt, name in CANDIDATES:
        try:
            await app.send_message(chat, txt, reply_markup=kb)
            print("OK", name, "%.4x" % ord(txt), flush=True)
        except Exception as e:
            print("FAIL", name, "%.4x" % ord(txt), type(e).__name__, str(e)[:60], flush=True)
        await asyncio.sleep(1)
    await app.stop()


asyncio.run(main())