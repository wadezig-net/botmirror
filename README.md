```markdown
# botmirror

> A self-hosted, all-in-one Telegram mirror bot that downloads from the web and uploads to your favorite cloud storage. Built with Pyrogram.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Pyrogram](https://img.shields.io/badge/Pyrogram-2.0.106-9cf)](https://docs.pyrogram.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
- [Usage](#-usage)
- [Commands](#-commands)
- [Project Structure](#-project-structure)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🚀 Overview

**botmirror** is a Telegram bot designed for mirroring files from the internet to cloud storage or directly to Telegram. It handles direct links, torrents, and a wide range of file-hosting services, with built-in support for proxy rotation, captcha solving, and premium link unlocking.

The project is written in Python using the **Pyrogram** framework and is optimized for deployment on a VPS or a self-hosted server.

---

## ✨ Features

- **Multi-Source Downloading**
  - Direct HTTP/HTTPS links
  - Torrent & Magnet links
  - YouTube and other sites via `yt-dlp`
  - File-hosting services: GoFile, 1fichier, Terabox, SFl.gl, DevUploads, Mega.nz
- **Multi-Destination Uploading**
  - Google Drive
  - GoFile
  - Telegram (as document or media)
- **Advanced Proxy Support**
  - Single proxy or rotating proxy list
  - Integration with PetaniProxy gateway for rotating IPs
  - Force IPv4 to avoid IPv6 routing issues on VPS
- **Captcha Solving**
  - 2Captcha integration for Cloudflare Turnstile / reCAPTCHA
  - Sandbox mode for testing without credits
- **Premium Link Unlocking**
  - Debrid-Link API support for premium hosters
  - qBittorrent API for torrent management
- **User & Admin Management**
  - Premium user system with balance and top-up
  - Admin commands for user promotion, backup, and credential scanning
- **Task Management**
  - Live status of active tasks
  - Cancel running tasks
  - File renaming and removal after upload
- **UI/UX**
  - Inline keyboards with a consistent "Back" button
  - Command menu registered directly in Telegram
  - Status UI with progress and system stats

---

## 🛠 Tech Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.10+ |
| Bot Framework | Pyrogram 2.0.106 |
| Media Download | yt-dlp, aria2, requests |
| Cloud Upload | Google Drive API, GoFile API |
| Torrent | qBittorrent API |
| Captcha | 2captcha-python |
| Proxy | PySocks, custom rotation |
| Browser Automation | Playwright, Node.js scripts |
| Process Management | PM2 (`ecosystem.config.js`) |

---

## 🏁 Getting Started

### Prerequisites

- **Python 3.10+**
- **Node.js** (for certain downloader scripts)
- **qBittorrent** (optional, for torrent support)
- **A Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- **Telegram API ID and API Hash** from [my.telegram.org](https://my.telegram.org/)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/wadezig-net/botmirror.git
   cd botmirror
```

2. Create and activate a virtual environment (recommended)
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install Python dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Install Node.js dependencies (if using browser-based downloaders)
   ```bash
   npm install
   ```
5. Seed the yt-dlp EJS cache (optional, for offline VPS)
   ```bash
   # The repository includes a pre-seeded cache at cache/yt-dlp
   # No action needed unless you want to refresh it
   ```

Configuration

1. Copy the example environment file
   ```bash
   cp .env.example .env
   ```
   If .env.example does not exist, create a .env file manually.
2. Edit .env and fill in the required values
   ```env
   # Telegram API credentials
   API_ID=123456
   API_HASH=your_api_hash_here
   BOT_TOKEN=your_bot_token_here
   
   # Base directory (default: /root/botmirror)
   BASE_DIR=/root/botmirror
   
   # Proxy configuration (optional)
   DOWNLOAD_PROXY=http://user:pass@host:port
   # or
   DOWNLOAD_PROXIES_FILE=/path/to/proxies.txt
   
   # 2Captcha (optional)
   CAPTCHA_API_KEY=your_2captcha_key
   CAPTCHA_SANDBOX=false
   
   # Debrid-Link (optional)
   DEBRID_LINK_API_KEY=your_debrid_key
   
   # Google Drive (optional)
   # Place your credentials.json in the project root
   ```
3. Set up Google Drive (optional)
   · Follow the Google Drive API quickstart to create OAuth credentials.
   · Save the credentials as credentials.json in the project root.
   · Run python gdrive_auth.py once to authenticate.
4. Configure PM2 (optional, for production)
   ```bash
   pm2 start ecosystem.config.js
   ```

---

🎮 Usage

Start the bot:

```bash
python bot.py
```

On first run, the bot will register its command menu with Telegram. You can then interact with it in any chat where the bot is a member.

Basic Workflow

1. Send /start to see the main menu.
2. Use /mirror <URL> or reply to a message with /mirror to mirror a file.
3. Use /torrent <magnet> to download and mirror a torrent.
4. Check progress with /status.
5. Cancel a task with /cancel (inside the task's inline keyboard).

---

🤖 Commands

Command Description
/start Start the bot and show the main menu
/mirror / /m Mirror a URL or reply to a message/media
/torrent / /t Download and mirror a torrent/magnet
/status View active tasks and history
/cancel Cancel a running task
/backup Send a PDF backup to the chat (admin only)
/rm Remove an uploaded file
/help Show usage guide
/saldo Check your balance and free quota
/topup Info on how to top up balance
/donate Support the development
/addpremium Add a premium user (admin only)
/delpremium Remove a premium user (admin only)
/addadmin Promote a user to admin (owner only)
/deladmin Demote an admin (owner only)
/userlist List premium, owner, and admin users
/scan Scan paths for API keys/secrets (owner only)
/checkcreds Inspect a replied message for credentials (owner only)

---

📁 Project Structure

```
botmirror/
├── bot.py                  # Entry point, command registration
├── config.py               # Environment variables and constants
├── requirements.txt        # Python dependencies
├── package.json            # Node.js dependencies
├── ecosystem.config.js     # PM2 process manager config
├── downloader/             # Download logic (HTTP, yt-dlp, torrent, etc.)
├── uploader/               # Upload logic (Google Drive, GoFile, Telegram)
├── handlers/               # Telegram command and callback handlers
├── scripts/                # Node.js and Python helper scripts
├── cache/yt-dlp/           # Pre-seeded yt-dlp cache for offline VPS
├── emotes-premium/         # Premium emoji assets
├── downloads/              # Temporary download directory
├── gdrive_auth.py          # Google Drive authentication script
├── custom_emoji.py         # Custom emoji handling
├── status_ui.py            # Status message UI builder
├── system_stats.py         # System resource monitoring
└── utils.py                # Shared utilities
```

---

🤝 Contributing

Contributions are welcome! Since this is an early-stage project, there is plenty of room to improve documentation, add tests, and refactor code.

1. Fork the repository.
2. Create a feature branch (git checkout -b feat/your-feature).
3. Commit your changes (git commit -m 'feat: add some feature').
4. Push to the branch (git push origin feat/your-feature).
5. Open a Pull Request.

Please make sure your code follows the existing style and includes tests where applicable.

---

📄 License

This project is licensed under the MIT License. See the LICENSE file for details.

---

⚠️ Disclaimer

This bot is intended for personal and educational use. Please respect the terms of service of the websites and services you interact with. The maintainers are not responsible for any misuse of this software.

```
