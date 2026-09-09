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
    app = Client("mirrorbot_fetch", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    packs = {
        "GemsEmoji": ["💎"],
        "tgaic": ["⭐", "🔗", "🗑", "⚙"],
        "qip_status": ["❌", "👤", "🟢"],
        "PremiumIcons": ["🔄", "⭐", "🏷", "🛡", "🔒", "📄", "🎁", "⚡"],
        "Premium_Icon": ["🔄", "🚀", "🛡", "🚫", "🔒", "📄"],
        "EmojiStatus": ["⭐", "🟢", "🔧", "❤️", "🚫"],
        "developeremojis": ["☕", "❔"],
        "AdventureTimeEmojis": ["👑", "⏳"],
        "CatPawsEmoji": ["💎", "🔧", "🙏", "📖", "🎁"],
        "BirthdayCollection": ["💡", "👑", "🎁"],
        "LoadingEmoji": ["🔄", "❌", "🌐", "✅", "🔹", "⏳"],
        "OneUI_Icons": ["🔄", "👤", "🗂", "🔍", "💡", "🛡", "🔒", "📄"],
    }
    by_nfe = {nfe(e): e for e in WANT}
    chosen = {}
    for name, emos in packs.items():
        try:
            r = await app.invoke(
                raw.functions.messages.GetStickerSet(
                    stickerset=raw.types.InputStickerSetShortName(short_name=name), hash=0
                )
            )
        except Exception:
            print(f"GET {name} FAILED")
            continue
        alts = {}
        for d in (r.documents or []):
            for a in d.attributes:
                if isinstance(a, raw.types.DocumentAttributeCustomEmoji):
                    alts[a.alt] = d.id
        for e in emos:
            key = nfe(e)
            if key in chosen or key not in alts:
                continue
            chosen[key] = alts[key]
            print(f"  {e:4s} <- {name}")

    # verify each chosen id is truly a custom-emoji document
    uniq_ids = list(dict.fromkeys(chosen.values()))
    ok = set()
    if uniq_ids:
        res = await app.invoke(raw.functions.messages.GetCustomEmojiDocuments(document_id=uniq_ids))
        for doc in res:
            if not isinstance(doc, raw.types.Document):
                continue
            if any(isinstance(a, raw.types.DocumentAttributeCustomEmoji) for a in doc.attributes):
                ok.add(doc.id)

    final = {}
    for key in chosen:
        if chosen[key] in ok:
            final[by_nfe[key]] = chosen[key]

    prev = {}
    map_path = os.path.join("emotes-premium", "official_map.json")
    if os.path.exists(map_path):
        prev = json.load(open(map_path, encoding="utf-8"))
    prev.update(final)
    json.dump(prev, open(map_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    covered = [e for e in WANT if e in prev]
    print(f"final covered: {len(covered)}/{len(WANT)}")
    print("missing:", " ".join(e for e in WANT if e not in prev))
    for e in covered:
        print("  OK", e, prev[e])
    await app.stop()


asyncio.get_event_loop().run_until_complete(main())