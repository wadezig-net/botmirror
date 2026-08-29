import os
import re
import time
import uuid
import shutil
import asyncio

from pyrogram import filters

from config import app, DOWNLOAD_DIR, task_registry
from status_ui import render_status
from downloader.torrent import download_torrent, zip_folder, fetch_torrent_file
from uploader.gofile import upload_to_gofile

# magnet link & link .torrent
MAGNET_RE = re.compile(r"magnet:\S+")
TORRENT_URL_RE = re.compile(r"https?://\S+\.torrent\b[^\s]*|https?://\S+\.torrent")

# informasi tambahan yang mastik ke magnet (nama, arah dl), biar dipakai jadi judul
INFO_NAME_RE = re.compile(r"&dn=([^&]+)")


def extract_torrent_input(message):
    """Ambil magnet/torrent link dari argumen command atau dari pesan yang di-reply."""
    if len(message.command) >= 2:
        return message.command[1]

    replied = message.reply_to_message
    if replied:
        text = replied.text or replied.caption or ""
        m = MAGNET_RE.search(text)
        if m:
            return m.group(0)
        m = TORRENT_URL_RE.search(text)
        if m:
            return m.group(0)

    return None


def friendly_title(link):
    """Buat judul yang gampang dibaca dari magnet (nama dari &dn=) atau biarkan mentah."""
    if link.startswith("magnet:"):
        m = INFO_NAME_RE.search(link)
        if m:
            return m.group(1).replace("+", " ")[:60]
        return "Magnet Link"
    return os.path.basename(link)


def tidy_torrent_url(url: str) -> str:
    """Bersihkan URL .torrent dari query string (mis. ?from=mediafire) biar
    friendly_title nggak kepanjangan dan download file .torrent lebih bersih."""
    if re.search(r"\.torrent(?:\?|$)", url):
        return re.sub(r"\.torrent.*$", r".torrent", url)
    return url


@app.on_message(filters.command(["torrent", "t"]))
async def torrent(client, message):
    import utils  # import lokal biar gaol-gagal kalau modul lain error
    if not utils.is_premium(message.from_user.id):
        return await message.reply(
            "❌ Maaf, Anda belum memiliki akses premium.\n"
            f"🆔 User ID: `{message.from_user.id}`\n\n"
            "Silahkan hubungi owner @waaadezig untuk aktivasi."
        )

    link = extract_torrent_input(message)
    if not link:
        return await message.reply(
            "❌ Nggak ada link torrent/magnet.\n\n"
            "Gunakan:\n"
            "`/torrent magnet:?xt=urn:btih:...`\n"
            "`/torrent https://.../file.torrent`\n"
            "atau reply ke pesan yang berisi magnet/torrent link dengan `/torrent`"
        )

    request_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(DOWNLOAD_DIR, request_id)
    os.makedirs(work_dir, exist_ok=True)

    status_msg = await message.reply("🧲 Menyiapkan torrent...")

    ctx = {
        "status": status_msg,
        "last_text": [""],
        "request_id": request_id,
        "user_id": message.from_user.id if message.from_user else 0,
        "user_mention": message.from_user.mention() if message.from_user else "Unknown",
        "start_time": time.monotonic(),
        "title": friendly_title(link),
        "process": None,
        "phase": "Menyiapkan",
        "percent": 0.0,
        "task": asyncio.current_task(),
    }
    task_registry[request_id] = ctx

    try:
        await render_status(ctx, "Torrent", percent=0.0)

        torrent_input = link
        # kalau user kasih URL .torrent (bukan magnet), download file .torrent-nya
        # dulu biar lancar ke aria2c (dan gak tergantung tracker mati di magnet).
        if link.startswith("http://") or link.startswith("https://"):
            if not re.search(r"\.torrent(?:\?|$)", link):
                raise Exception(
                    "URL HTTP bukan file .torrent. Gunakan magnet:/magnet:?xt=... "
                    "atau URL yang berakhiran .torrent."
                )
            await render_status(ctx, "Mengunduh file .torrent...", percent=0.0)
            torrent_input = await fetch_torrent_file(tidy_torrent_url(link), work_dir)

        files = await download_torrent(torrent_input, work_dir, ctx)

        targets = [f for f in files if os.path.isfile(f)]
        if not targets:
            raise Exception("Tidak ada file yang berhasil didownload dari torrent.")

        # kalau cuma 1 file, upload langsung; kalau banyak file, zip dulu
        if len(targets) == 1:
            ctx["title"] = os.path.basename(targets[0])
            upload_file = targets[0]
        else:
            await render_status(ctx, "Mengarsipkan", percent=0.0)
            upload_file = zip_folder(work_dir, ctx)
            ctx["title"] = os.path.basename(upload_file)

        upload_result = await upload_to_gofile(upload_file, ctx)

        size_mb = os.path.getsize(upload_file) / 1024 / 1024

        await message.reply(
            "✅ Torrent selesai\n\n"
            f"📁 File:\n{os.path.basename(upload_file)}\n\n"
            f"📦 Size: {size_mb:.2f} MB\n\n"
            f"🔗 Link:\n{upload_result.get('link')}"
        )

    except asyncio.CancelledError:
        try:
            await ctx["status"].edit("🚫 Torrent dibatalkan.")
        except Exception:
            pass
        raise

    except Exception as e:
        try:
            await ctx["status"].edit(f"❌ Gagal:\n{e}")
        except Exception:
            pass

    finally:
        try:
            await ctx["status"].delete()
        except Exception:
            pass
        task_registry.pop(request_id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
