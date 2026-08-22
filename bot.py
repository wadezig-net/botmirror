import asyncio

from pyrogram.types import BotCommand, MenuButtonCommands

from config import app

# import handlers supaya semua @app.on_message / @app.on_callback_query
# ke-daftar ke client (efek samping dari import, wajib ada meski "unused")
import handlers.menu
import handlers.status_cmd
import handlers.cancel
import handlers.mirror


async def register_commands(client):
    """Daftarin command menu (muncul saat user ketik '/' di chat)."""
    await client.set_bot_commands([
        BotCommand("start", "Mulai / tampilkan menu utama"),
        BotCommand("mirror", "Mirror URL atau reply ke pesan/media"),
        BotCommand("m", "Alias singkat buat /mirror"),
        BotCommand("status", "Lihat task aktif & riwayat"),
        BotCommand("help", "Panduan penggunaan bot"),
        BotCommand("donate", "Dukung pengembangan bot"),
    ])

    # tombol "Menu" di sebelah ikon stiker/attach -- tap sekali langsung
    # nampilin semua command sebagai list, tanpa perlu ketik "/" dulu
    await client.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main():
    await app.start()
    await register_commands(app)
    print("Bot berjalan...")
    await asyncio.Event().wait()  # jalan terus sampai proses dihentikan


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
