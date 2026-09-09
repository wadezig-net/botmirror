import asyncio
import os
import re
import time

from config import QB_HOST, QB_PORT, QB_USER, QB_PASS, QB_TIMEOUT
from status_ui import render_status


def qb_configured():
    return bool(QB_HOST and QB_USER and QB_PASS)


def _make_client():
    import qbittorrentapi

    client = qbittorrentapi.Client(
        host=QB_HOST, port=QB_PORT, username=QB_USER, password=QB_PASS
    )
    client.auth_log_in()
    return client


def _find_file(directory, name):
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f == name:
                return os.path.join(root, f)
    return None


async def torrent_download(url, work_dir, ctx):
    """Download torrent/magnet lewat qBittorrent. Torrent disimpan langsung ke
    work_dir (save_path), file terbesar diberi prioritas, tunggu selesai lalu
    kembalikan path file-nya."""
    if not qb_configured():
        raise Exception(
            "Torrent/magnet butuh qBittorrent. Isi QB_HOST, QB_PORT, QB_USER, "
            "QB_PASS di .env (web UI qBittorrent aktif di server ini) kalau mau "
            "mode torrent."
        )

    await render_status(ctx, "🧲 qBittorrent: menambahkan torrent...")
    try:
        client = await asyncio.to_thread(_make_client)
    except Exception as e:
        raise Exception(f"Gagal konek ke qBittorrent: {e}") from e

    btih = None
    if url.startswith("magnet:"):
        m = re.search(r"btih:([a-fA-F0-9]{40})", url)
        if m:
            btih = m.group(1).lower()

    await asyncio.to_thread(client.torrents_add, urls=url, save_path=work_dir)

    torrent = None
    waited = 0
    while waited < 90:
        await asyncio.sleep(2)
        waited += 2
        torrents = await asyncio.to_thread(client.torrents_info)
        torrent = next((t for t in torrents if btih and str(t.hash).lower() == btih), None)
        if torrent is None and not btih and torrents:
            torrent = max(torrents, key=lambda t: t.added_on)
        if torrent:
            break
    if torrent is None:
        raise Exception(
            "Torrent tidak muncul di qBittorrent dalam 90 detik. Magnet butuh "
            "metadata dari tracker -- cek state di konsol qBittorrent."
        )

    def _prioritize():
        files = client.torrents_files(torrent_hash=torrent.hash)
        largest = max(files, key=lambda f: f.size)
        for i, f in enumerate(files):
            client.torrents_file_priority(
                torrent_hash=torrent.hash, file_ids=i,
                priority=(7 if i == largest.id else 0),
            )
        return largest.name

    try:
        largest_name = await asyncio.to_thread(_prioritize)
    except Exception as e:
        raise Exception(f"Gagal membaca daftar file torrent: {e}") from e

    base_name = os.path.basename(largest_name)
    deadline = time.monotonic() + QB_TIMEOUT
    last_ui = [0.0]

    while time.monotonic() < deadline:
        info = await asyncio.to_thread(client.torrents_info, torrent_hashes=torrent.hash)
        if not info:
            raise Exception("Torrent hilang dari qBittorrent.")
        t = info[0]

        if t.state in ("error", "missingFiles"):
            raise Exception(
                f"qBittorrent melaporkan error untuk torrent ini (state: {t.state}). "
                "Cek konsol qBittorrent (tracker down / file hilang / disk penuh)."
            )

        now = time.monotonic()
        if now - last_ui[0] >= 2.5:
            last_ui[0] = now
            await render_status(
                ctx, "⬇️ qBittorrent", percent=t.progress * 100,
                processed=t.downloaded, total=t.size or None,
                speed=t.dlspeed or None, eta=str(t.eta) if t.eta else None,
            )

        if t.progress >= 1.0:
            target = os.path.join(work_dir, base_name)
            if os.path.isfile(target):
                return target
            found = await asyncio.to_thread(_find_file, work_dir, base_name)
            if found:
                return found

        await asyncio.sleep(2)

    raise Exception(
        f"⏱️ Torrent timeout setelah {int(QB_TIMEOUT)} detik -- seeder lambat atau "
        "link mati. Cek state di qBittorrent."
    )