import os
import re
import uuid
import shutil

from pyrogram import filters

from config import app, pending_upload, task_registry
from downloader.ytdlp import download_via_url
from downloader.telegram_dl import download_from_telegram

from uploader.gofile import upload_to_gofile
from uploader.gdrive import upload_to_gdrive
from uploader.history import add_entry

SAFE_NAME_RE = re.compile(r"[\\/\0]")


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
