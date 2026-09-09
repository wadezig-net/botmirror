"""CLI tes pipeline 2Captcha end-to-end (tanpa sentuh bot) — via SDK resmi.

Dipakai buat nge-verifikasi bahwa:
  - CAPTCHA_API_KEY di .env valid
  - sandbox mode berjalan (kalau CAPTCHA_SANDBOX=1) --> gratis, solusi simulasi
  - solve_turnstile -> token berfungsi

Demo Turnstile punya 2captcha sendiri dipakai sebagai target (sitekey publik
`3x00000000000000000000FF` di https://2captcha.com/demo/cloudflare-turnstile),
jadi tes ini nggak perlu URL eksternal lain.

Jalankan:
  cd /root/botmirror
  venv/bin/python -m downloader.captcha_test
"""
import sys
import asyncio

from downloader import captcha
from config import CAPTCHA_SANDBOX

DEMO_SITEKEY = "3x00000000000000000000FF"
DEMO_URL = "https://2captcha.com/demo/cloudflare-turnstile"


def _color(txt, code):
    if not sys.stdout.isatty():
        return txt
    return f"\033[{code}m{txt}\033[0m"


async def _run():
    print(_color("== Tes pipeline 2Captcha (Turnstile demo) ==", "1;36"))

    configured = captcha.captcha_configured()
    if not configured:
        print(_color("[FAIL] CAPTCHA_API_KEY kosong di .env", "1;31"))
        print("       1. Daftar (gratis): https://2captcha.com")
        print("       2. Ambil API key di dasbor -> Settings -> API key")
        print("       3. Isi CAPTCHA_API_KEY=<key> di /root/botmirror/.env")
        print("       4. Jalankan ulang tes ini.")
        return 1

    if CAPTCHA_SANDBOX:
        print(_color("[ ~ ] Sandbox flag triaktif; JANGAN LUPA aktifkan juga sandbox", "1;33"))
        print(_color("      di https://2captcha.com/setting#sandbox biar gratis.", "1;33"))
    else:
        print(_color("[ ~ ] Sandbox OFF -> task akan ngecharge saldo.", "1;33"))

    try:
        balance = await captcha.get_balance()
        print(f"[info] Saldo: ${balance:.3f}" if balance else
              f"[info] Saldo: $0.00 (kosong / sandbox)")
    except Exception as e:
        print(f"[warn] getBalance gagal: {e}")

    try:
        print(f"[  ] Solve Turnstile (sitekey: {DEMO_SITEKEY[:8]}...)...")
        solution = await captcha.solve_turnstile(
            sitekey=DEMO_SITEKEY,
            pageurl=DEMO_URL,
            timeout=120,
        )
    except Exception as e:
        print(_color(f"[FAIL] solve_turnstile: {e}", "1;31"))
        return 1

    token = solution.get("token")
    if not token:
        print(_color(f"[FAIL] Solusi tanpa token: {solution}", "1;31"))
        return 1

    print(f"[ok] Token diterima ({len(token)} karakter, awalan "
          f"'{token[:20]}...')")
    print(_color("== PIPELINE OK ==", "1;32"))
    print("Berikut langkahnya bisa langsung dipakai bot (sfl.gl) -- pastikan")
    print("CAPTCHA_SANDBOX di .env kosong / 0 kalau mau mode produksi berbayar.")
    return 0


def main():
    code = asyncio.run(_run())
    sys.exit(code)


if __name__ == "__main__":
    main()