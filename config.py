import os
from collections import deque
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_DIR = "/root/botmirror"
DOWNLOAD_DIR = f"{BASE_DIR}/downloads"
COOKIE_FILE = f"{BASE_DIR}/cookies.txt"
SFILE_SCRIPT = f"{BASE_DIR}/scripts/sfile_download.js"
FICHIER_SCRIPT = f"{BASE_DIR}/scripts/fichier_download.js"
TERABOX_SCRIPT = f"{BASE_DIR}/scripts/terabox_download.js"
DEVUPLOADS_SCRIPT = f"{BASE_DIR}/scripts/devuploads_download.js"
FICHIER_LOGIN_COOKIES = f"{BASE_DIR}/fichier_login_cookies.json"
FICHIER_PROXIES_FILE = f"{BASE_DIR}/fichier_proxies.txt"
NODE_BIN = "/usr/bin/node"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# task yang lagi jalan (request_id -> ctx dict), dan riwayat singkat task yang
# udah selesai/gagal/dibatalin -- dipakai buat /status
task_registry = {}
pending_upload = {}
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
