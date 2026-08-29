import os
import re
import asyncio

from status_ui import render_status

MEGA_DOMAINS = ("mega.nz", "mega.co.nz")

MEGADL_BIN = "/usr/bin/megadl"

UNIT_MULT = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}

# contoh baris progress megadl:
# <nama file>: 0.42% - 61.7 MiB (64705380 bytes) of 14.5 GiB (3.0 MiB/s)
PROGRESS_RE = re.compile(
    r":\s+([\d.]+)%\s+-\s+[\d.]+\D?(?:KiB|MiB|GiB|B)\s+\((\d+)\s+bytes\)\s+of\s+"
    r"([\d.]+)\D?(KiB|MiB|GiB|B)\s+\(([\d.]+)\D?(KiB|MiB|GiB|B)/s\)"
)


async def mega_download(url, work_dir, ctx):
    """
    Download file dari Mega.nz pakai megatools (megadl) -- CLI battle-tested
    yang udah paham skema enkripsi Mega, jauh lebih stabil daripada library
    Python yang sempat dicoba sebelumnya (banyak bug di versi barunya).
    """
    if not os.path.isfile(MEGADL_BIN):
        raise Exception(f"megadl tidak ditemukan di {MEGADL_BIN}. Install dulu: apt install megatools")

    await render_status(ctx, "⬇️ Download dari Mega.nz")

    proc = await asyncio.create_subprocess_exec(
        MEGADL_BIN, "--path", work_dir, url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    ctx["process"] = proc

    loop = asyncio.get_running_loop()
    last_update = [0.0]
    buf = b""

    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\r" in buf or b"\n" in buf:
            idx_r = buf.find(b"\r")
            idx_n = buf.find(b"\n")
            candidates = [i for i in (idx_r, idx_n) if i != -1]
            idx = min(candidates)
            line = buf[:idx].decode(errors="ignore").strip()
            buf = buf[idx + 1:]
            if not line:
                continue

            match = PROGRESS_RE.search(line)
            if match:
                now = loop.time()
                if now - last_update[0] < 2.5:
                    continue
                last_update[0] = now

                percent = float(match.group(1))
                processed = int(match.group(2))
                total_val = float(match.group(3))
                total_unit = match.group(4)
                speed_val = float(match.group(5))
                speed_unit = match.group(6)
                total_bytes = total_val * UNIT_MULT.get(total_unit, 1)
                speed_bytes = speed_val * UNIT_MULT.get(speed_unit, 1)

                await render_status(
                    ctx, "Download", percent=percent,
                    processed=processed, total=total_bytes, speed=speed_bytes,
                )

    ctx["process"] = None
    returncode = await proc.wait()
    if returncode != 0:
        raise Exception(f"megadl gagal (exit code {returncode})")

    candidates = [os.path.join(work_dir, f) for f in os.listdir(work_dir)]
    candidates = [f for f in candidates if os.path.isfile(f) and ".megatmp." not in f]
    if not candidates:
        raise Exception("File hasil download Mega.nz tidak ditemukan")
    return max(candidates, key=os.path.getmtime)
