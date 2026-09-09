import json
import os
import re

import pyrogram.utils as _utils
from pyrogram.raw.types import MessageEntityCustomEmoji

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.getenv("CUSTOM_EMOJI_MAP", os.path.join(BASE_DIR, "emotes-premium", "official_map.json"))

_EMO_MAP = {}
_RE = None


def load_map(path=MAP_FILE):
    global _EMO_MAP, _RE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    _EMO_MAP = {}
    for k, v in data.items():
        if not isinstance(v, int) and not str(v).isdigit():
            continue
        _EMO_MAP[k] = int(v)
    if _EMO_MAP:
        pattern = "(" + "|".join(
            sorted((re.escape(k.rstrip("\uFE0F")) + r"\uFE0F?" for k in _EMO_MAP), key=len, reverse=True)
        ) + ")"
        _RE = re.compile(pattern)
    else:
        _RE = None
    return _EMO_MAP


def _build_entities(text):
    if not _RE:
        return []
    entities = []
    for m in _RE.finditer(text):
        emo = m.group(0)
        emo_id = _EMO_MAP.get(emo) or _EMO_MAP.get(emo.rstrip("\uFE0F"))
        if emo_id is None:
            continue
        offset = len(text[: m.start()].encode("utf-16-le")) // 2
        length = len(emo.encode("utf-16-le")) // 2
        entities.append(MessageEntityCustomEmoji(offset=offset, length=length, document_id=emo_id))
    return entities


async def _patched_parse_text_entities(client, text, parse_mode, entities):
    if not entities and isinstance(text, str):
        extra = _build_entities(text)
        if extra:
            # entitas raw sudah dalam bentuk raw (bukan high-level) sehingga
            # tidak boleh lewat loop `await entity.write()` milik pyrogram
            # (keduanya: tidak punya atribut _client + write() menghasilkan bytes).
            return {"message": text, "entities": extra}
    return await _orig_parse_text_entities(client, text, parse_mode, entities)


_orig_parse_text_entities = None


def install():
    global _orig_parse_text_entities
    load_map()
    _orig_parse_text_entities = _utils.parse_text_entities
    _utils.parse_text_entities = _patched_parse_text_entities
    if _EMO_MAP:
        print(f"custom_emoji: {len(_EMO_MAP)} mapping aktif ({os.path.basename(MAP_FILE)})")
    else:
        print("custom_emoji: map kosong, pakai emoji teks biasa")
    return _EMO_MAP