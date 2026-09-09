import asyncio
import os
import re
import time

import requests

from config import (
    DEBRID_LINK_API_KEY,
    DEBRID_LINK_HOST,
    DEBRID_LINK_TIMEOUT,
    DEBRID_LINK_DOMAINS,
    requests_proxies,
)
from status_ui import render_status
from utils import clean_filename

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def debrid_configured():
    return bool((DEBRID_LINK_API_KEY or "").strip())


def _extract_error(payload):
    """Ambil pesan error paling masuk akal dari payload Debrid-Link (yang bentuknya
    bisa beda-beda tergantung endpoint/status)."""
    if not isinstance(payload, dict):
        return "respon tidak dikenal"
    value = payload.get("value")
    if isinstance(value, dict) and value.get("error"):
        err = value["error"]
        if isinstance(err, dict):
            return str(err.get("message") or err.get("error") or err)
        return str(err)
    for key in ("message", "error", "reason"):
        if payload.get(key):
            return str(payload[key])
    return f"HTTP {payload.get('status_code', '')}".strip() or "unknown error"


async def debrid_link_unlock(url):
    """Unlock URL host premium jadi direct link via Debrid-Link API v2
    (POST /downloader/add). Balikin dict hasil API:
    {id, name, url, downloadUrl, host, size}.

    Raise Exception dengan pesan jelas buat user kalau gagal."""
    if not debrid_configured():
        raise Exception(
            "DEBRID_LINK_API_KEY belum di-set di .env. "
            "Daftar akun di debrid-link.com lalu ambil API key di "
            "https://debrid-link.com/account/ (bagian API)."
        )

    api = DEBRID_LINK_HOST.rstrip("/")

    def do_unlock():
        r = requests.post(
            f"{api}/downloader/add",
            headers={
                "Authorization": f"Bearer {DEBRID_LINK_API_KEY.strip()}",
                "User-Agent": UA,
                "Accept": "application/json",
            },
            data={"url": url},
            timeout=DEBRID_LINK_TIMEOUT,
            proxies=requests_proxies(),
        )
        r.raise_for_status()
        return r.json()

    try:
        payload = await asyncio.to_thread(do_unlock)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 401:
            raise Exception(
                "Debrid-Link menolak API key (401 Unauthorized). "
                "Cek DEBRID_LINK_API_KEY di .env / status akun debrid-link."
            ) from e
        if status in (402, 403):
            raise Exception(
                f"Debrid-Link menolak (HTTP {status}) -- cek kuota/tarif akun premium debrid-link."
            ) from e
        raise Exception(f"Gagal menghubungi Debrid-Link: {e}") from e
    except requests.RequestException as e:
        raise Exception(f"Gagal menghubungi Debrid-Link: {e}") from e
    except ValueError:
        raise Exception("Respon dari Debrid-Link bukan JSON.") from None

    if not isinstance(payload, dict):
        raise Exception("Respon dari Debrid-Link tidak dikenal.")

    if not payload.get("success"):
        raise Exception(f"Debrid-Link menolak unlock: {_extract_error(payload)}")

    value = payload.get("value")
    if not isinstance(value, dict):
        raise Exception("Debrid-Link tidak mengembalikan data download.")
    if value.get("expired") is True:
        raise Exception("Link Debrid-Link sudah expired / file tidak ditemukan di host asal.")
    err = value.get("error")
    if isinstance(err, str) and err.strip() and err.strip().lower() not in ("ok", ""):
        raise Exception(f"Debrid-Link error: {err}")

    download_url = value.get("downloadUrl")
    if not download_url:
        raise Exception("Debrid-Link tidak mengembalikan downloadUrl.")

    return {
        "id": value.get("id"),
        "name": value.get("name") or "",
        "url": value.get("url") or url,
        "downloadUrl": download_url,
        "host": value.get("host"),
        "size": value.get("size") or value.get("fileSize") or 0,
    }


async def debrid_link_download(url, work_dir, ctx):
    """Unlock link lewat Debrid-Link lalu download direct link-nya ke work_dir.
    Balikin path file hasil download."""
    await render_status(ctx, "🔓 Unlock via Debrid-Link...")

    info = await debrid_link_unlock(url)

    host = info.get("host")
    label = f"Debrid-Link ({host})" if host else "Debrid-Link"
    await render_status(ctx, f"✨ {label} -> direct link")

    dl_url = info["downloadUrl"]
    loop = asyncio.get_running_loop()

    def do_download():
        with requests.get(
            dl_url, stream=True, timeout=60, headers={"User-Agent": UA},
            proxies=requests_proxies(),
        ) as r:
            r.raise_for_status()

            # kalau yang balik halaman HTML (bukan file beneran), jangan dilanjut --
            # kemungkinan token/signature link-nya already expired.
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                raise Exception(
                    "Direct link dari Debrid-Link balikin halaman HTML, bukan file. "
                    "Coba lagi atau cek status akun premium debrid-link."
                )

            cd = r.headers.get("content-disposition", "")
            match = re.search(r'filename="?([^";]+)"?', cd)
            if match:
                fname = clean_filename(match.group(1))
            else:
                fname = clean_filename(info.get("name"))
                if not fname or fname == "downloaded_file.bin":
                    fname = clean_filename(dl_url.split("?")[0].split("/")[-1]) or "download.bin"

            filepath = os.path.join(work_dir, fname)
            total = int(r.headers.get("content-length", info.get("size") or 0))
            downloaded = 0
            start = time.monotonic()
            last_update = [0.0]

            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    if now - last_update[0] < 2.5 and downloaded < total:
                        continue
                    last_update[0] = now

                    elapsed = max(now - start, 0.001)
                    speed = downloaded / elapsed
                    percent = (downloaded / total * 100) if total else 0
                    asyncio.run_coroutine_threadsafe(
                        render_status(
                            ctx, "Download", percent=percent,
                            processed=downloaded, total=total or None, speed=speed,
                        ),
                        loop,
                    )

            return filepath

    return await asyncio.to_thread(do_download)