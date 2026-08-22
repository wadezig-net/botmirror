from pyrogram import filters

from config import app, task_registry


@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_callback(client, callback_query):
    request_id = callback_query.data.split("_", 1)[1]
    ctx = task_registry.get(request_id)

    if not ctx:
        await callback_query.answer("Task tidak ditemukan / sudah selesai", show_alert=True)
        return

    # kill subprocess (yt-dlp/node) kalau lagi jalan, biar nggak nyisa proses nganggur
    process = ctx.get("process")
    if process:
        try:
            process.kill()
        except Exception:
            pass

    task = ctx.get("task")
    if task:
        task.cancel()

    await callback_query.answer("Membatalkan task...")


@app.on_message(filters.regex(r"^/cancel_(\w+)"))
async def cancel_cmd(client, message):
    # dukung /cancel_<id> yang dikirim manual (bukan cuma dari tombol)
    request_id = message.matches[0].group(1)
    ctx = task_registry.get(request_id)
    if not ctx:
        await message.reply("❌ Task tidak ditemukan / sudah selesai")
        return

    process = ctx.get("process")
    if process:
        try:
            process.kill()
        except Exception:
            pass

    task = ctx.get("task")
    if task:
        task.cancel()

    await message.reply("🚫 Membatalkan task...")
