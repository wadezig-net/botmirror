import asyncio
import os
import re
import uuid
import zipfile

import requests

from status_ui import render_status
from downloader.http_direct import generic_http_download

# x.com / twitter.com (link status video/gambar)
X_DOMAINS = ("x.com", "twitter.com")

STATUS_RE = re.compile(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)")

TW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://x.com/",
    "Accept": "video/mp4,image/*,*/*",
}


def _fetch_tweet_info(username, status_id):
    """Ambil info tweet via API fxtwitter (tanpa login). Return dict atau None."""
    api_url = f"https://api.fxtwitter.com/{username}/status/{status_id}"
    resp = requests.get(api_url, timeout=30, headers={"User-Agent": TW_HEADERS["User-Agent"]})
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 200:
        return None
    return payload.get("tweet") or None


def _pick_best_video(media):
    """Ambil URL MP4 tertinggi dari media video (pilih bitrate terbesar)."""
    best = None
    for fmt in media.get("formats", []):
        if fmt.get("container") == "mp4":
            if best is None or (fmt.get("bitrate") or 0) > (best.get("bitrate") or 0):
                best = fmt
    if best is None:
        return media.get("url")
    return best.get("url")


async def x_download(url, work_dir, ctx):
    """Resolver X/Twitter.

    yt-dlp extractornya kadang gagal buat beberapa tweet (terutama *amplify
    video* / iklan berbayar yang media-nya tak ter-expose di halaman). Di sini
    kita ambil langsung lewat API fxtwitter yang nge-listing media asli tanpa
    login, lalu download URL MP4/gambar-nya secara langsung.
    """
    m = STATUS_RE.search(url)
    if not m:
        raise Exception(
            "Cuma bisa mirror link X/Twitter berformat `/username/status/<id>`."
        )
    status_id = m.group(2)
    username = m.group(1)

    await render_status(ctx, "🌐 Mengambil info tweet dari X")

    try:
        tweet = await asyncio.to_thread(_fetch_tweet_info, username, status_id)
    except Exception:
        tweet = None

    if not tweet:
        raise Exception(
            "Gagal ambil info tweet (mungkin link salah / tweet dihapus / akun privat)."
        )

    media_all = (tweet.get("media") or {}).get("all") or []
    if not media_all:
        raise Exception(
            "Tweet ini nggak punya media (cuma teks) — nggak ada yang bisa di-mirror."
        )

    urls = []
    for media in media_all:
        mtype = media.get("type")
        if mtype == "video":
            v = _pick_best_video(media) or media.get("url")
            if v:
                urls.append(v)
        elif media.get("url"):
            urls.append(media["url"])

    if not urls:
        raise Exception("Media tweet tidak bisa diambil URL download-nya.")

    downloaded = []
    for i, u in enumerate(urls):
        await render_status(ctx, f"Download media {i + 1}/{len(urls)}")
        try:
            fpath = await generic_http_download(u, work_dir, ctx, headers=TW_HEADERS)
            downloaded.append(fpath)
        except Exception:
            continue

    if not downloaded:
        raise Exception("Semua media tweet gagal di-download.")

    # satu file -> langsung; banyak file -> zip jadi satu arsip
    if len(downloaded) == 1:
        return downloaded[0]

    archive = os.path.join(work_dir, f"x_media_{uuid.uuid4().hex[:6]}.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in downloaded:
            zf.write(f, arcname=os.path.basename(f))
    return archive