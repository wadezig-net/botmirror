from pyrogram import filters

from config import app
from status_ui import render_status_overview, status_keyboard


@app.on_message(filters.command("status"))
async def status_cmd(client, message):
    await message.reply(render_status_overview(), reply_markup=status_keyboard())


@app.on_callback_query(filters.regex("^status_open$"))
async def status_open_callback(client, callback_query):
    await callback_query.message.reply(render_status_overview(), reply_markup=status_keyboard())
    await callback_query.answer()


@app.on_callback_query(filters.regex("^status_refresh$"))
async def status_refresh_callback(client, callback_query):
    try:
        await callback_query.message.edit_text(
            render_status_overview(), reply_markup=status_keyboard()
        )
        await callback_query.answer("Diperbarui")
    except Exception:
        await callback_query.answer("Belum ada perubahan")
