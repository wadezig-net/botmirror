from pyrogram import filters
from config import app, pending_upload


@app.on_callback_query(filters.regex(r"^upload_zip:"))
async def zip_select(client, callback_query):
    request_id = callback_query.data.split(":")[1]

    task = pending_upload.get(request_id)

    if not task:
        return await callback_query.answer(
            "❌ Task sudah expired",
            show_alert=True
        )

    task["zip"] = True

    upload_keyboard = task["upload_keyboard"]

    await callback_query.message.edit_text(
        "📦 File akan di-zip dulu.\n\n"
        "📤 Pilih tujuan upload:",
        reply_markup=upload_keyboard
    )

    await callback_query.answer()
