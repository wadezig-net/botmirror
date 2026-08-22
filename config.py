import os
from collections import deque
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_DIR = "/root/wadezig"
DOWNLOAD_DIR = f"{BASE_DIR}/downloads"
COOKIE_FILE = f"{BASE_DIR}/cookies.txt"
SFILE_SCRIPT = f"{BASE_DIR}/scripts/sfile_download.js"
NODE_BIN = "/root/.local/share/pi-node/node-v22.23.2-linux-x64/bin/node"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# task yang lagi jalan (request_id -> ctx dict), dan riwayat singkat task yang
# udah selesai/gagal/dibatalin -- dipakai buat /status
task_registry = {}
task_history = deque(maxlen=20)

app = Client(
    "mirrorbot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    # kalau kena FLOOD_WAIT singkat (di bawah threshold ini), Pyrogram otomatis
    # nunggu & retry sendiri alih-alih langsung raise exception dan gagal update
    sleep_threshold=30,
)
