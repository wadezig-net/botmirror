import os
import re
import time
import asyncio
import requests

from utils import clean_filename
from status_ui import render_status


async def generic_http_download(url, work_dir, ctx, headers=None):
    """
    Fallback untuk URL yang tidak didukung yt-dlp (mis. direct-link file host
    seperti sfile atau media storage lain yang cuma serve file mentah lewat HTTP).
    Tidak berlaku untuk Terabox dkk yang butuh token/signature hasil render JS.
    """
    loop = asyncio.get_running_loop()

    def do_download():
        with requests.get(url, stream=True, timeout=60, headers=headers or {}) as r:
            r.raise_for_status()

            # kalau yang balik itu halaman HTML (bukan file beneran), berarti ini
            # bukan direct-link -- kemungkinan shortlink/share-page yang butuh JS
            # buat nampilin link asli. Gagal jelas di sini, jangan sampe kepupuk
            # ke-upload sebagai "file" yang isinya cuma halaman web.
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                raise Exception(
                    "URL ini balikin halaman HTML, bukan file langsung. "
                    "Kemungkinan ini shortlink/share-page yang butuh proses render JS "
                    "(bukan direct-download link) -- yt-dlp juga nggak berhasil extract dari sini."
                )

            # coba ambil nama file dari header, fallback ke bagian akhir URL
            cd = r.headers.get("content-disposition", "")
            match = re.search(r'filename="?([^";]+)"?', cd)
            if match:
                fname = clean_filename(match.group(1))
            else:
                fname = clean_filename(url.split("/")[-1].split("?")[0]) or "file.bin"

            filepath = os.path.join(work_dir, fname)
            total = int(r.headers.get("content-length", 0))
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
                            processed=downloaded, total=total, speed=speed,
                        ),
                        loop,
                    )

            return filepath

    return await asyncio.to_thread(do_download)
