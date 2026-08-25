from pyrogram import filters
from config import app, pending_upload


@app.on_callback_query(filters.regex(r"^rename_yes:"))
async def rename_yes(client, callback_query):
    request_id = callback_query.data.split(":")[1]

    task = pending_upload.get(request_id)

    if not task:
        return await callback_query.answer(
            "❌ Task sudah expired",
            show_alert=True
        )

    task["waiting_rename"] = True

    msg = await callback_query.message.reply(
        "✏️ Kirim nama file baru:\n\n"
        "Contoh:\n"
        "`Film Anime 1080p.mp4`"
    )
    task.setdefault("extra_messages", []).append(msg)

    await callback_query.answer()


@app.on_callback_query(filters.regex(r"^rename_no:"))
async def rename_no(client, callback_query):
    request_id = callback_query.data.split(":")[1]

    task = pending_upload.get(request_id)

    if not task:
        return await callback_query.answer(
            "❌ Task sudah expired",
            show_alert=True
        )

    upload_keyboard = task["upload_keyboard"]

    await callback_query.message.edit_text(
        "📤 Pilih tujuan upload:",
        reply_markup=upload_keyboard
    )

    await callback_query.answer()


@app.on_message(filters.text & ~filters.regex(r"^/"))
async def receive_filename(client, message):
    for request_id, task in pending_upload.items():
        if task.get("waiting_rename"):
            filename = message.text.strip()

            task["filename"] = filename
            task["waiting_rename"] = False

            msg1 = await message.reply(
                f"✅ Nama file:\n"
                f"`{filename}`\n\n"
                "📤 Silahkan pilih tujuan upload."
            )

            msg2 = await message.reply(
                "📤 Pilih tujuan upload:",
                reply_markup=task["upload_keyboard"]
            )

            task.setdefault("extra_messages", []).extend([msg1, msg2])

            return
