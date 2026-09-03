"""Security handler for Hermes bot — owner-only commands."""

from utils import is_owner
from tools.api_key_scanner import scan_for_secrets, format_scan_report


async def handle_scan(client, message):
    """Scan a path for leaked API keys — owner only.
    Usage: /scan <path>  (default: current working directory)
    Example: /scan /root/botmirror
             /scan .
    """
    if not is_owner(message.from_user.id):
        return

    # Extract path from command (everything after "/scan ")
    parts = message.text.split(" ", 1)
    path = parts[1].strip() if len(parts) > 1 else "."

    await message.reply(f"🔍 Scanning `{path}`...")

    try:
        results = scan_for_secrets(path)
        msg = format_scan_report(results)
        await message.reply(msg)
    except Exception as e:
        await message.reply(f"❌ Scan error: `{str(e)}`")


async def handle_telegram_check(client, message):
    """Quick check if a Telegram message contains leaked credentials — owner only.
    Usage: /checkcreds (replies to the message being checked)
    """
    if not is_owner(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.text:
        await message.reply("⚠️ Reply ke pesan yang ingin diperiksa.")
        return

    text = message.reply_to_message.text or ""
    from tools.api_key_scanner import scan_telegram_message
    leaked = scan_telegram_message(text)
    if leaked:
        await message.reply("⚠️ **MUNGKIN ADA KREDENSIAL YANG BOCOR!**\n\n"
                            "Periksa pesan ini — mungkin mengandung API key, token, atau secret.")
    else:
        await message.reply("✅ Pesan tidak mengandung pola kredensial yang dikenali.")