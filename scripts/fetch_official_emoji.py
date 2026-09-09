import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyrogram import raw

from config import API_ID, API_HASH, BOT_TOKEN

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_JSON = os.path.join(BASE_DIR, "emotes-premium", "official_map.json")

EMOJIS = [
    "❌", "⚠️", "✅", "💎", "📁", "📦", "🚫", "🔒", "📤", "❤️", "📥",
    "👤", "⏱", "⏳", "🟢", "🌐", "☕", "🔗", "🔄", "📡", "⬇", "⬆",
    "🙏", "🗑", "📊", "✏", "☁", "📨", "📄", "💡", "⚙", "🚀", "🔧",
    "🏷", "🔹", "🗂", "❔", "🎁", "🧾", "⚡", "📖", "⏩", "📂", "⭐",
    "👑", "🛡", "→", "🧲", "📭", "🔍",
]

def resolve_input(name):
    cls_name = f"InputStickerSet{name}"
    cls = getattr(raw.types, cls_name, None)
    if cls is not None:
        try:
            return cls()
        except TypeError:
            pass
    return raw.types.InputStickerSetShortName(short_name=name)


# Set resmi Telegram (tanpa premium). Yang berawalan "AnimatedEmoji"/"Emoji"
# adalah tipe runtime (di-resolve server), sisanya shortname paket warna
# premium yang juga bisa dipakai semua orang.
CANDIDATE_SETS = [
    "AnimatedEmoji",
    "AnimatedEmojiAnimations",
    "EmojiGenericAnimations",
    "AnimatedEmojis",
    "PremiumAnimatedStar",
    "PremiumPink",
    "PremiumRed",
    "PremiumPurple",
    "PremiumYellow",
    "PremiumGreen",
    "PremiumWhite",
    "PremiumBlue",
    "PremiumOrange",
    "PremiumGold",
]

WANTED = set(EMOJIS)


def emoji_doc_map(sticker_set):
    """Maps alt-emoji -> custom_emoji_id (document id) from a raw StickerSet."""
    out = {}
    docs = getattr(sticker_set, "documents", None) or getattr(sticker_set, "stickers", []) or []
    for doc in docs:
        alt = None
        for attr in doc.attributes:
            if isinstance(attr, raw.types.DocumentAttributeSticker):
                alt = attr.alt
                break
        if not alt:
            continue
        emoji_id = getattr(doc, "id", None)
        if not emoji_id:
            continue
        for key in (alt, alt.strip("\uFE0F")):
            out[key] = emoji_id
    return out


async def main():
    from pyrogram import Client

    app = Client("mirrorbot_fetch", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    merged = {}
    await app.start()
    try:
        inputs = [resolve_input(n) for n in CANDIDATE_SETS]
        for name, ist in zip(CANDIDATE_SETS, inputs):
            try:
                r = await app.invoke(
                    raw.functions.messages.GetStickerSet(stickerset=ist, hash=0)
                )
                docmap = emoji_doc_map(r)
                n = len(getattr(r, "documents", None) or getattr(r, "stickers", []) or [])
                hit = {e: docmap[e] for e in WANTED if e in docmap}
                merged.update(hit)
                print(f"set {name:22s} total={n:4d} hit={len(hit):2d}")
            except Exception as e:
                print(f"set {name:22s} ERROR {type(e).__name__}: {e}")
    finally:
        await app.stop()

    print("\n--- coverage ---")
    for emo in EMOJIS:
        print(f"  {'OK' if emo in merged else '..'}  {emo}  {merged.get(emo, '')}")
    covered = sum(1 for e in EMOJIS if e in merged)
    print(f"\ncovered {covered}/{len(EMOJIS)}")
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print("wrote", OUT_JSON)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())