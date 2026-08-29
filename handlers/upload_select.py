import os
import re
import asyncio
import zipfile
import uuid
import shutil

from pyrogram import filters

from config import app, pending_upload, task_registry
from status_ui import render_status
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram

from uploader.gofile import upload_to_gofile
from uploader.gdrive import upload_to_gdrive
from uploader.history import add_entry

SAFE_NAME_RE = re.compile(r"[\\/\0]")


async def make_zip(work_dir, target_file, ctx):
    """Bungkus file hasil download jadi arsip .zip di work_dir, return path arsip."""
    base = os.path.basename(target_file)
    name_no_ext, _ext = os.path.splitext(base)
    zip_name = os.path.join(work_dir, f"{name_no_ext}.zip")

    total_size = os.path.getsize(target_file)

    await render_status(ctx, "Membuat ZIP", percent=0.0, processed=0, total=total_size)

    def zip_worker():
        with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
            # arsip berisi file dengan nama aslinya (tanpa path work_dir)
            zf.write(target_file, arcname=base)

    await asyncio.to_thread(zip_worker)
    await render_status(ctx, "Membuat ZIP", percent=100.0, processed=total_size, total=total_size)

    return zip_name


@app.on_callback_query(filters.regex(r"^upload_(gofile|gdrive):"))
async def upload_select(client, callback_query):

    data = callback_query.data

    upload_type, request_id = data.split(":")

    task = pending_upload.get(request_id)

    if not task:
        return await callback_query.answer(
            "❌ Task sudah tidak tersedia",
            show_alert=True
        )


    await callback_query.answer(
        "⏳ Memulai mirror..."
    )


    ctx = task["ctx"]
    url = task["url"]
    replied = task["replied"]
    work_dir = task["work_dir"]
    message = task["message"]

    # simpan task asyncio yang BENAR-BENAR menjalankan proses download+upload ini.
    # Sebelumnya ctx["task"] di-set dari handler /mirror (yang sudah selesai begitu
    # user klik tombol upload), jadi tombol /cancel_ cuba membatalkan task lama yang
    # sudah mati -> proses tidak pernah benar-benar dibatalkan.
    ctx["task"] = asyncio.current_task()


    try:
        if url:
            downloaded_file = await download_via_url(
                url,
                work_dir,
                ctx
            )
        else:
            downloaded_file = await download_from_telegram(
                replied,
                work_dir,
                ctx
            )

        # terapkan rename kalau user sempat pilih rename & ketik nama baru,
        # ekstensi asli tetap dipertahankan biar file tidak rusak
        if task.get("filename"):
            base, ext = os.path.splitext(downloaded_file)
            safe_name = SAFE_NAME_RE.sub("", task["filename"]).strip()
            if safe_name:
                new_path = os.path.join(work_dir, f"{safe_name}{ext}")
                os.rename(downloaded_file, new_path)
                downloaded_file = new_path

        ctx["title"] = os.path.basename(downloaded_file)

        # kalau user milih "Zip & Upload", bungkus file hasil download jadi arsip
        # .zip sebelum di-upload. Nama arsip mengikuti nama file (termasuk hasil
        # rename), tapi dengan ekstensi .zip.
        if task.get("zip"):
            await render_status(ctx, "Membuat ZIP", percent=0.0)
            downloaded_file = await make_zip(work_dir, downloaded_file, ctx)

        if upload_type == "upload_gofile":

            upload_result = await upload_to_gofile(
                downloaded_file,
                ctx
            )

        else:

            upload_result = await upload_to_gdrive(
                downloaded_file,
                ctx
            )


        size_mb = os.path.getsize(downloaded_file) / 1024 / 1024


        short_id = uuid.uuid4().hex[:6]

        entry = {
            "type": "gofile" if upload_type == "upload_gofile" else "gdrive",
            "file_id": upload_result.get("file_id"),
            "guest_token": upload_result.get("guest_token"),
            "name": os.path.basename(downloaded_file),
            "user_id": ctx["user_id"],
            "link": upload_result.get("link"),
        }

        add_entry(short_id, entry)

        await message.reply(
            "✅ Mirror selesai\n\n"
            f"📁 File:\n{os.path.basename(downloaded_file)}\n\n"
            f"📦 Size: {size_mb:.2f} MB\n\n"
            f"🔗 Link:\n{upload_result.get('link')}\n\n"
            f"🆔 ID: `{short_id}`"
        )


    except asyncio.CancelledError:
        # user menekan tombol / command cancel. Status panel sudah dihapus di
        # finally; di sini cukup kabari user kalau task-nya udah dibatalkan.
        try:
            await ctx["status"].edit("🚫 Task dibatalkan.")
        except Exception:
            pass
        raise

    except Exception as e:

        await message.reply(
            f"❌ Gagal:\n{e}"
        )


    finally:

        try:
            await ctx["status"].delete()
        except Exception:
            pass

        for m in task.get("extra_messages", []):
            try:
                await m.delete()
            except Exception:
                pass

        pending_upload.pop(request_id, None)
        task_registry.pop(request_id, None)

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )
