import asyncio
import hashlib
import os
import threading
import time
from urllib.parse import urlparse

from config import JD_EMAIL, JD_PASSWORD, JD_DEVICE, JD_TIMEOUT, JD_DOMAINS
from status_ui import render_status
from utils import clean_filename

# Sesi MyJDownloader valid ~30 menit; refresh/reconnect otomatis kalau kedaluwarsa
# atau request gagal (pola yang sama dengan self-debrid).
SESSION_REFRESH_SECONDS = 30 * 60
PACKAGE_WAIT_S = 45
POLL_INTERVAL = 2.0
STATUS_UI_INTERVAL = 2.5

_client = {"myjd": None, "device": None, "connected": False, "connected_at": 0.0}
_lock = threading.RLock()


def jd_configured():
    return bool(JD_EMAIL and JD_PASSWORD)


def _connect():
    """Bikin sesi MyJDownloader baru. Caller harus pegang lock."""
    import myjdapi

    jd = myjdapi.Myjdapi()
    jd.set_app_key("BOTMIRROR_APP")
    jd.connect(JD_EMAIL, JD_PASSWORD)
    jd.update_devices()
    device = jd.get_device(JD_DEVICE)
    if device is None:
        raise RuntimeError(
            f"Device JDownloader '{JD_DEVICE}' tidak ditemukan. Cek nama device "
            "di Settings -> My.JDownloader (contoh: JDownloader@email)."
        )
    return jd, device


def _ensure_connected():
    with _lock:
        fresh = (
            _client["connected"]
            and _client["myjd"] is not None
            and _client["device"] is not None
            and time.monotonic() - _client["connected_at"] < SESSION_REFRESH_SECONDS
        )
        if fresh:
            return True
        try:
            myjd, device = _connect()
            _client.update(myjd=myjd, device=device, connected=True, connected_at=time.monotonic())
            return True
        except Exception as e:
            _client.update(myjd=None, device=None, connected=False, connected_at=0.0)
            print(f"[jdownloader] koneksi gagal: {e}")
            return False


def _mark_disconnected():
    with _lock:
        _client.update(myjd=None, device=None, connected=False, connected_at=0.0)


def _suggest_name(url):
    parsed = urlparse(url)
    base = os.path.basename(parsed.path.split("?")[0])
    if not base:
        base = os.path.basename(parsed.query[:64]) or "download.bin"
    fname = clean_filename(base)
    return fname or "download.bin"


def _wait_for_package(linkgrabber, package_name, timeout_s):
    waited = 0
    while waited < timeout_s:
        time.sleep(2)
        waited += 2
        try:
            for pkg in linkgrabber.query_packages() or []:
                if package_name in (pkg.get("name") or ""):
                    return pkg
        except Exception as e:
            print(f"[jdownloader] query package gagal: {e}")
            _mark_disconnected()
            return None
    return None


def _jd_add(url, work_dir, file_id):
    """Tambahkan link ke linkgrabber JDownloader, pindah ke download list, mulai
    download. Sync: dipanggil lewat asyncio.to_thread. Balikin dict info."""
    if not _ensure_connected():
        raise Exception(
            "Gagal terhubung ke JDownloader. Pastikan JDownloader 2 jalan & login "
            "MyJDownloader dengan akun yang sama (JD_EMAIL/JD_PASSWORD), dan "
            "JD_DEVICE sesuai nama device di JDownloader."
        )

    device = _client["device"]
    package_name = f"MIRROR_{file_id}"

    try:
        device.linkgrabber.add_links([{
            "autostart": False,
            "links": url,
            "packageName": package_name,
            "destinationFolder": work_dir,
            "overwritePackagizerRules": True,
        }])
    except Exception as e:
        _mark_disconnected()
        raise Exception(f"Gagal menambahkan link ke JDownloader: {e}") from e

    package = _wait_for_package(device.linkgrabber, package_name, PACKAGE_WAIT_S)
    if not package:
        raise Exception(
            "Link tidak muncul di linkgrabber JDownloader. Host biasanya tidak "
            "didukung plugin JDownloader atau link-nya mati/butuh premium."
        )

    links = device.linkgrabber.query_links([
        {"packageUUIDs": [package["uuid"]], "maxResults": -1}
    ])
    if not links:
        raise Exception("Tidak ada link di package JDownloader.")
    link = links[0]

    chosen = _suggest_name(url)
    try:
        device.linkgrabber.rename_link(link["uuid"], chosen)
    except Exception:
        chosen = None  # discovery yang cari nama asli

    device.linkgrabber.move_to_downloadlist([], [package["uuid"]])

    time.sleep(1)
    try:
        device.downloads.force_download([link["uuid"]], [package["uuid"]])
    except Exception:
        try:
            device.downloadcontroller.start_downloads()
        except Exception:
            pass

    return {"uuid": link["uuid"], "package_uuid": package["uuid"], "rename_to": chosen}


def _query_status(package_uuid):
    """Ambil progres download dari downloads.query_links (polling)."""
    if not _ensure_connected():
        return None
    try:
        links = _client["device"].downloads.query_links([
            {"packageUUIDs": [package_uuid]}
        ]) or []
        if not links:
            return None
        link = links[0]
        processed = link.get("bytesLoaded") or 0
        total = link.get("bytesTotal") or 0
        return {
            "processed": processed,
            "total": total,
            "percent": (processed / total * 100) if total else None,
            "finished": bool(link.get("finished")),
            "status": link.get("status") or "",
            "speed": link.get("speed"),
        }
    except Exception as e:
        print(f"[jdownloader] query status gagal: {e}")
        return None


def _dl_in_progress(work_dir):
    for entry in os.scandir(work_dir):
        name = entry.name
        if entry.is_file() and name.lower().endswith((".part", ".crdownload")):
            return True
        if entry.is_dir() and name.startswith("__dl__"):
            return True
    return False


def _discover_file(work_dir):
    best = None
    for entry in os.scandir(work_dir):
        if not entry.is_file() or entry.name.startswith("__dl__"):
            continue
        if entry.name.lower().endswith((".part", ".crdownload")):
            continue
        try:
            size = entry.stat().st_size
        except Exception:
            continue
        if best is None or size > best[1]:
            best = (entry.path, size)
    return best[0] if best else None


async def _wait_complete(work_dir, package_uuid, ctx):
    deadline = time.monotonic() + JD_TIMEOUT
    last_ui = [0.0]

    while time.monotonic() < deadline:
        status = await asyncio.to_thread(_query_status, package_uuid)
        now = time.monotonic()
        if status and now - last_ui[0] >= STATUS_UI_INTERVAL:
            last_ui[0] = now
            await render_status(
                ctx, "⬇️ JDownloader", percent=status["percent"],
                processed=status["processed"] or None,
                total=status["total"] or None,
                speed=status.get("speed") or None,
            )
            lowered = (status["status"] or "").lower()
            if status["finished"] or "finish" in lowered or "complete" in lowered:
                filepath = _discover_file(work_dir)
                if filepath:
                    return filepath

        filepath = _discover_file(work_dir)
        if filepath and not _dl_in_progress(work_dir):
            return filepath

        await asyncio.sleep(POLL_INTERVAL)

    raise Exception(
        f"⏱️ JDownloader timeout ({int(JD_TIMEOUT)} detik). Banyak filehost gratis "
        "punya cooldown 5-10 menit antar download; cek queue JDownloader."
    )


async def jdownloader_download(url, work_dir, ctx):
    """Download link host premium lewat JDownloader (MyJDownloader). File ditulis
    langsung ke work_dir oleh JDownloader, lalu dikembalikan path-nya."""
    if not jd_configured():
        raise Exception(
            "JDownloader belum dikonfigurasi. Isi JD_EMAIL, JD_PASSWORD, JD_DEVICE "
            "di .env. Butuh JDownloader 2 yang jalan di mesin yang sama dengan bot "
            "dan akun my.jdownloader.org."
        )

    file_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    await render_status(ctx, "📥 Kirim ke JDownloader (linkgrabber)...")
    info = await asyncio.to_thread(_jd_add, url, work_dir, file_id)
    await render_status(ctx, "⬇️ JDownloader men-download...")
    return await _wait_complete(work_dir, info["package_uuid"], ctx)