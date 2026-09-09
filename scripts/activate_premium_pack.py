import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyrogram import Client, raw

from config import API_ID, API_HASH, BOT_TOKEN

WANT = [
    "❌", "⚠️", "✅", "💎", "📁", "📦", "🚫", "🔒", "📤", "❤️", "📥",
    "👤", "⏱", "⏳", "🟢", "🌐", "☕", "🔗", "🔄", "📡", "⬇", "⬆",
    "🙏", "🗑", "📊", "✏", "☁", "📨", "📄", "💡", "⚙", "🚀", "🔧",
    "🏷", "🔹", "🗂", "❔", "🎁", "🧾", "⚡", "📖", "⏩", "📂", "⭐",
    "👑", "🛡", "→", "🧲", "📭", "🔍",
]


def nfe(e):
    return e.rstrip("\uFE0F")


async def main():
    short = os.environ.get("SHORTNAME") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not short:
        print("pakai: SHORTNAME=NamaPack python3 scripts/activate_premium_pack.py")
        return
    short = short.replace("t.me/addemoji/", "").replace("@", "")

    app = Client("mirrorbot_fetch", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="..")
    await app.start()
    try:
        sr = await app.invoke(raw.functions.messages.GetStickerSet(
            stickerset=raw.types.InputStickerSetShortName(short_name=short), hash=0))
        alts = {}
        for d in (sr.documents or []):
            for a in d.attributes:
                if isinstance(a, raw.types.DocumentAttributeCustomEmoji):
                    alts[a.alt] = d.id
        print(f"pack {short}: {len(alts)} custom emoji docs")

        # validasi id => benar-benar custom emoji
        uniq = list(dict.fromkeys(alts.values()))
        res = await app.invoke(raw.functions.messages.GetCustomEmojiDocuments(document_id=uniq))
        valid = {d.id for d in res if isinstance(d, raw.types.Document)
                 and any(isinstance(a, raw.types.DocumentAttributeCustomEmoji) for a in d.attributes)}
        print(f"terverifikasi {len(valid)}/{len(uniq)}")

        bby = {}
        for e in WANT:
            for key in (e, nfe(e)):
                if key in alts and alts[key] in valid:
                    bby[e] = alts[key]
                    break

        # emoji alt lain yang bisa dipakai message entity (beda dari WANT) — jangan dimuat
        over = sorted(alts)[:20]
        print(f"cocok WANT: {len(bby)}/{len(WANT)}")
        print("missing:", " ".join(e for e in WANT if e not in bby))
        print("contoh alt di pack:", " ".join(over))

        if bby:
            bby["_meta"] = {"pack": short, "covered": len([k for k in bby if not k.startswith("_")])}
            with open("emotes-premium/official_map.json", "w", encoding="utf-8") as f:
                json.dump(bby, f, ensure_ascii=False, indent=2)
            print("official_map.json ditulis. Restart bot: pm2 restart botmirror")
    finally:
        await app.stop()


asyncio.get_event_loop().run_until_complete(main())