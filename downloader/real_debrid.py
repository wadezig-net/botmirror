import asyncio
import os
import re
import time

import requests

from config import (
    RD_API_KEY,
    RD_HOST,
    RD_TIMEOUT,
    RD_DOMAINS,
    requests_proxies,
)
from status_ui import render_status
from utils import clean_filename

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def rd_configured():
    """Bot pakai Real-Debrid cuma kalau RD_API_KEY di-set di .env.
    Token bisa diambil di https://real-debrid.com/apitoken (akun punya sendiri)."""
    return bool((RD_API_KEY or "").strip())


def _extract_error(payload):
    """Ambil pesan error dari respon JSON Real-Debrid (biasanya key 'error')."""
    if not isinstance(payload, dict):
        return "respon tidak dikenal"
    return (
        payload.get("error")
        or payload.get("message")
        or str(payload)
    )


async def real_debrid_unlock(url, password=""):
    """Unlock link host premium jadi direct link via Real-Debrid
    (POST /unrestrict/link). Balikin dict hasilnya:
    {id, filename, filesize, download, host}.

    Raise Exception dengan pesan jelas buat user kalau gagal."""
    if not rd_configured():
        raise Exception(
            "RD_API_KEY belum di-set di .env. Ambil token API Real-Debrid di "
            "https://real-debrid.com/apitoken (akun punya sendiri), lalu isi RD_API_KEY."
        )

    api = RD_HOST.rstrip("/")

    def do_unlock():
        r = requests.post(
            f"{api}/unrestrict/link",
            headers={
                "Authorization": f"Bearer {RD_API_KEY.strip()}",
                "User-Agent": UA,
                "Accept": "application/json",
            },
            data={"link": url, "password": password},
            timeout=RD_TIMEOUT,
            proxies=requests_proxies(),
        )
        r.raise_for_status()
        return r.json()

    try:
        payload = await asyncio.to_thread(do_unlock)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        try:
            detail = e.response.json() if e.response is not None else {}
        except ValueError:
            detail = {}
        if status == 401:
            raise Exception(
                "Real-Debrid menolak API token (401 / bad_token). Cek RD_API_KEY di .env."
            ) from e
        if status == 403:
            code = detail.get("error_code")
            if code == 20:
                raise Exception(
                    "Real-Debrid menolak unlock: host ini butuh akun PREMIUM "
                    "(error hoster_not_free). Upgrade dulu di real-debrid.com."
                ) from e
            raise Exception(
                f"Real-Debrid menolak (403): {_extract_error(detail)}"
                + (" -- cek status akun premium di real-debrid.com." if code != 22 else "")
            ) from e
        if status == 400:
            raise Exception(
                f"Real-Debrid tidak menerima link ini (400: {_extract_error(detail)}). "
                "Cek format link / apakah hostnya didukung."
            ) from e
        raise Exception(f"Gagal menghubungi Real-Debrid (HTTP {status}): {e}") from e
    except requests.RequestException as e:
        raise Exception(f"Gagal menghubungi Real-Debrid: {e}") from e
    except ValueError:
        raise Exception("Respon dari Real-Debrid bukan JSON.") from None

    if not isinstance(payload, dict):
        raise Exception("Respon dari Real-Debrid tidak dikenal.")

    if payload.get("error"):
        raise Exception(f"Real-Debrid menolak unlock: {_extract_error(payload)}")

    download_url = payload.get("download")
    if not download_url:
        raise Exception("Real-Debrid tidak mengembalikan link download.")

    return {
        "id": payload.get("id"),
        "name": payload.get("filename") or payload.get("name") or "",
        "link": payload.get("link") or url,
        "download": download_url,
        "host": payload.get("host"),
        "size": payload.get("filesize") or 0,
    }


async def real_debrid_download(url, work_dir, ctx, password=""):
    """Unlock link lewat Real-Debrid lalu download direct link-nya ke work_dir.
    Balikin path file hasil download."""
    await render_status(ctx, "🔓 Unlock via Real-Debrid...")

    info = await real_debrid_unlock(url, password=password)

    host = info.get("host")
    label = f"Real-Debrid ({host})" if host else "Real-Debrid"
    await render_status(ctx, f"✨ {label} -> direct link")

    dl_url = info["download"]
    loop = asyncio.get_running_loop()

    def do_download():
        with requests.get(
            dl_url, stream=True, timeout=60, headers={"User-Agent": UA},
            proxies=requests_proxies(),
        ) as r:
            r.raise_for_status()

            # kalau yang balik halaman HTML (bukan file beneran), jangan dilanjut --
            # kemungkinan token/signature direct link-nya sudah expired.
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type or content_type.startswith("application/x-httpd"):
                raise Exception(
                    "Direct link Real-Debrid balikin halaman HTML, bukan file. "
                    "Coba lagi atau cek status akun real-debrid."
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