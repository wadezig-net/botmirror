import os
import json
import asyncio

from config import BASE_DIR, NODE_BIN
from status_ui import render_status
from downloader.browser_link_capture import run_node_link_finder, download_resolved_link

SFL_SCRIPT = f"{BASE_DIR}/scripts/sfl_download.js"
SFL_CAPTCHA_SCRIPT = f"{BASE_DIR}/scripts/sfl_captcha.js"

# sfl.gl -- gerbang iklan bertingkat, tujuan akhirnya biasanya link sfile.mobi/sfile.co
SFL_DOMAINS = ("sfl.gl",)


async def sfl_headless_download(url, work_dir, ctx):
    """
    sfl.gl bukan file-host beneran -- dia gerbang iklan (scroll+klik+tunggu timer,
    berulang lewat beberapa tab) yang ujungnya ngarah ke situs file-host lain
    (biasanya sfile.mobi). Script node-nya cuma nembus gerbang itu dan balikin
    URL tujuan akhir, lalu kita serahkan ke handler sfile yang udah ada.
    """
    await render_status(ctx, "🌐 Membuka headless browser (melewati gerbang iklan)")

    if not os.path.isfile(SFL_SCRIPT):
        raise Exception(f"Script {SFL_SCRIPT} tidak ditemukan.")
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    # proses ini bisa makan waktu lumayan lama (beberapa putaran gerbang iklan,
    # tiap putaran ada timer ~15-25 detik) -- kasih timeout lebih longgar
    result = await run_node_link_finder(NODE_BIN, SFL_SCRIPT, url, work_dir, ctx, timeout=240)

    return await _handle_gate_result(result, url, work_dir, ctx)


async def sfl_captcha_download(url, work_dir, ctx):
    """
    Sama kayak sfl_headless_download, tapi dua-pass dengan 2Captcha:
      pass 1: buka gerbang, deteksi Cloudflare Turnstile
      pass 2 (kalau ada captcha): solve lewat 2Captcha, inject token, lanjut
    Lewat jalur ini sfl.gl yang tadinya "nggak didukung otomatis karena captcha"
    bisa di-mirror langsung, selama CAPTCHA_API_KEY di-set di .env.
    """
    if not os.path.isfile(SFL_CAPTCHA_SCRIPT):
        raise Exception(f"Script {SFL_CAPTCHA_SCRIPT} tidak ditemukan.")
    if not os.path.isfile(NODE_BIN):
        raise Exception(
            f"Node binary tidak ditemukan di {NODE_BIN}. "
            "Cek ulang lokasi node dengan `which node` dan update NODE_BIN di config.py."
        )

    from downloader import captcha as captcha_mod
    if not captcha_mod.captcha_configured():
        raise Exception(
            "CAPTCHA_API_KEY belum di-set di .env, padahal gerbang sfl.gl "
            "kemungkinan butuh Turnstile. Kasih key-nya dulu, atau mirror manual."
        )

    await render_status(ctx, "🕸️ Cek gerbang sfl.gl & deteksi captcha (2Captcha)")

    # Pass 1: deteksi Turnstile + kumpulin sitekey/pageurl
    pass1 = await _run_node_args(NODE_BIN, SFL_CAPTCHA_SCRIPT, url, work_dir, ctx, timeout=240)
    await render_status(ctx, "🔐 Mengecek kebutuhan captcha...")

    if pass1.get("captcha"):
        ts = pass1["captcha"]
        await render_status(ctx, "🧩 Turnstile terdeteksi, menyelesaikan lewat 2Captcha...")

        token_result = await captcha_mod.solve_turnstile(
            sitekey=ts.get("sitekey"),
            pageurl=ts.get("pageurl") or url,
            action=ts.get("action"),
            data=ts.get("data"),
            pagedata=ts.get("pagedata"),
            timeout=120,
        )
        token = token_result.get("token") or token_result.get("gRecaptchaResponse")
        if not token:
            raise Exception("2Captcha balikin solusi tanpa token.")

        await render_status(ctx, "✅ Captcha solved, lanjut lewatin gerbang")
        # Pass 2: inject token, traverse sisa gerbang
        pass1 = await _run_node_args(
            NODE_BIN, SFL_CAPTCHA_SCRIPT, url, work_dir, ctx,
            extra_args=[token], timeout=240,
        )

    return await _handle_gate_result(pass1, url, work_dir, ctx)


async def _run_node_args(node_bin, script, url, work_dir, ctx, extra_args=None,
                         timeout=240):
    """Jalankan script node dengan arg tambahan (token), parse stdout JSON.
    Mirip run_node_link_finder tapi ngedukung extra_args."""
    clean_env = os.environ.copy()
    for var in ("NODE_CHANNEL_FD", "NODE_UNIQUE_ID", "NODE_CHANNEL_SERIALIZATION_MODE"):
        clean_env.pop(var, None)

    proc = await asyncio.create_subprocess_exec(
        node_bin, script, url, work_dir, *(extra_args or []),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=clean_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise Exception(
            f"Headless browser timeout (>{timeout}s) saat proses gerbang sfl.gl."
        )

    stderr_text = stderr.decode(errors="ignore")
    for line in stderr_text.splitlines():
        print(line)  # keep pm2 logs buat debugging

    output = stdout.decode(errors="ignore").strip()
    try:
        if not output:
            raise Exception("Script sfl_captcha nggak produce output.")
        result = json.loads(output.splitlines()[-1])
    except Exception:
        raise Exception(f"Gagal parse output sfl_captcha: {output[:500]}")

    if not result.get("ok"):
        raise Exception(f"Gagal lewatin gerbang sfl.gl: {result.get('error')}")
    return result


async def _handle_gate_result(result, url, work_dir, ctx):
    """Seragamkan hasil gate (redirect_to / direct_url) jadi file yang di-download."""
    from downloader.sfile import (
        sfile_headless_download,
        sfile_http2_download,
        SFILE_DOMAINS,
    )

    redirect_url = result.get("redirect_to")
    if redirect_url:
        await render_status(ctx, "🔗 Sampai di tujuan akhir, lanjut proses sfile")
        if any(d in redirect_url for d in SFILE_DOMAINS):
            try:
                return await sfile_http2_download(redirect_url, work_dir, ctx)
            except Exception:
                return await sfile_headless_download(redirect_url, work_dir, ctx)
        # kalau ternyata tujuan akhirnya bukan sfile (situs lain yang belum kita
        # kenal), tetep coba anggap sebagai direct link biasa
        result = {"direct_url": redirect_url, "referer": url, "filename": None}

    return await download_resolved_link(result, work_dir, ctx)