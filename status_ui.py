import time

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import task_registry, task_history
from utils import get_progress_bar, format_bytes, format_duration, is_owner_or_premium
from system_stats import render_system_block


async def progress_edit(message, text, last_text_holder, reply_markup=None):
    # avoid spamming edit_text with identical content (Telegram rate-limits this)
    if last_text_holder[0] == text:
        return
    last_text_holder[0] = text
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        # jangan silent -- kalau ada FloodWait/error lain, minimal kelihatan di pm2 logs
        print(f"[progress_edit] gagal update status: {e}")


def cancel_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Cancel", callback_data=f"cancel_{request_id}")]
    ])


async def render_status(ctx, status_label, percent=None, processed=None, total=None,
                         speed=None, eta=None, mode="#Mirror"):
    """Render tampilan status ala leech-bot (progress bar, info user, system stats)."""
    # kalau task ini udah selesai/dihapus dari registry (panel-nya juga udah kehapus),
    # jangan coba edit lagi -- ini bisa kejadian kalau ada update "telat" dari background
    # thread yang baru sempet jalan setelah task-nya sendiri udah declared selesai.
    if ctx["request_id"] not in task_registry:
        return

    ctx["phase"] = status_label
    if percent is not None:
        ctx["percent"] = percent

    elapsed = format_duration(time.monotonic() - ctx["start_time"])

    title_display = "Private Task" if is_owner_or_premium(ctx.get("user_id")) else ctx['title']
    lines = [f"🔒 Nama: {title_display}", ""]

    if percent is not None:
        lines.append(f"Status: {status_label} ({percent:.2f}%)")
        lines.append(f"[{get_progress_bar(percent)}]")
    else:
        lines.append(f"Status: {status_label}")

    lines.append(f"👤 Oleh: {ctx['user_mention']}")
    lines.append(f"🆔 UserID: [{ctx['user_id']}]")
    lines.append(f"⏱ Waktu: {elapsed}")

    if total:
        lines.append(f"📦 Ukuran: {format_bytes(total)}")
    if processed is not None:
        lines.append(f"⚙️ Diproses: {format_bytes(processed)}")
    if eta:
        lines.append(f"⏳ Estimasi: {eta}")
    if speed is not None:
        lines.append(f"🚀 Kecepatan: {format_bytes(speed)}/s")

    lines.append("🔧 Engine: Pyrogram + yt-dlp")
    lines.append(f"🏷 Mode: {mode}")
    lines.append("")
    lines.append(render_system_block())

    text = "\n".join(lines)
    await progress_edit(ctx["status"], text, ctx["last_text"], reply_markup=cancel_keyboard(ctx["request_id"]))


def status_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh")]
    ])


def render_status_overview():
    lines = []

    if task_registry:
        lines.append(f"📡 **Task Aktif ({len(task_registry)})**\n")
        for ctx in task_registry.values():
            elapsed = format_duration(time.monotonic() - ctx["start_time"])
            percent = ctx.get("percent") or 0.0
            bar = get_progress_bar(percent)
            title_display = "🔒 Private Task" if is_owner_or_premium(ctx.get("user_id")) else ctx['title']
            mention_display = "🔒 Anonim" if is_owner_or_premium(ctx.get("user_id")) else ctx['user_mention']
            lines.append(
                f"🔹 {title_display}\n"
                f"   {ctx.get('phase', '?')} [{bar}] {percent:.1f}%\n"
                f"   👤 {mention_display} | ⏱ {elapsed}\n"
                f"   /cancel_{ctx['request_id']}"
            )
    else:
        lines.append("📡 **Task Aktif**\nTidak ada task yang lagi jalan.")

    if task_history:
        lines.append("\n🗂 **Riwayat Terakhir**\n")
        icons = {"done": "✅", "error": "❌", "cancelled": "🚫"}
        for h in list(task_history)[:10]:
            ago = format_duration(time.time() - h["finished_at"])
            icon = icons.get(h["state"], "❔")
            title_display = "🔒 Private Task" if is_owner_or_premium(h.get("user_id")) else h['title']
            mention_display = "🔒 Anonim" if is_owner_or_premium(h.get("user_id")) else h['user_mention']
            entry = f"{icon} {title_display} — {mention_display} ({ago} lalu)"
            if h["state"] == "error" and h["error"]:
                entry += f"\n   ⚠️ {h['error'][:100]}"
            lines.append(entry)

    lines.append("\n" + render_system_block())
    return "\n".join(lines)
