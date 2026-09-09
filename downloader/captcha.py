"""2Captcha client — wrap official `twocaptcha` SDK (2captcha-python) buat
bypass Cloudflare Turnstile / reCAPTCHA di gerbang shortlink (mis. sfl.gl)
sebelum tujuan akhir dikeluarkan.

Official repo: https://github.com/2captcha/2captcha-python  (MIT)
Kelas AsyncTwoCaptcha dipakai langsung: polling get_result ditangani SDK
(soal interval & timeout diatur dari config). Semua method non-blocking.

Hanya dipakai untuk link/flow yang AUTHORIZED (link yang dikirim user ke bot
buat di-mirror).
"""
import os
import logging

from twocaptcha import AsyncTwoCaptcha
from twocaptcha import (
    ApiException,
    NetworkException,
    TimeoutException,
    ValidationException,
)
# Penting: SDK punya DUA keluarga exception berbeda.
#   - exceptions.api.*     : di-raise oleh lapisan HTTP (async_api.in_/res)
#   - exceptions.solver.*  : di-re-export lewat top-level `twocaptcha.*`
# AsyncApiClient raise yang `api.*` (ejaklaan), sedangkan yang buat catch di
# async_solver import yang `solver.*`. Makanya kita wajib tangkap dua-duanya.
from twocaptcha.exceptions import api as _sdk_api_exc

from config import (
    CAPTCHA_API_KEY,
    CAPTCHA_HOST,
    CAPTCHA_POLL_INTERVAL,
    CAPTCHA_TIMEOUT,
    CAPTCHA_SANDBOX,
)

log = logging.getLogger("captcha")

# errorCode dari SDK / API v1 yang harus direport ke user (terjangkau)
_FRIENDLY_ERRORS = {
    "ERROR_WRONG_USER_KEY": "API key salah.",
    "ERROR_KEY_DOES_NOT_EXIST": "API key tidak terdaftar.",
    "ERROR_ZERO_BALANCE": "Saldo 2Captcha habis.",
    "ERROR_NO_SLOT_AVAILABLE": "Semua worker sibuk, coba lagi.",
    "ERROR_CAPTCHA_UNSOLVABLE": "Captcha tidak bisa diselesaikan (gagal).",
    "ERROR_BAD_DUPLICATES": "Solusi duplikat (rate-limited).",
    "ERROR_TASK_ABSENT": "Task tidak ketemu (expired).",
    "ERROR_TASK_NOT_SUPPORTED": "Tipe task ini nggak didukung.",
    "ERROR_IPADDR_NOT_ALLOWED": "IP tidak diizinkan akses API.",
    "ERROR_EMPTY_ACTION": "Parameter action kosong.",
}


class CaptchaError(Exception):
    pass


class CaptchaUnconfiguredError(CaptchaError):
    pass


class CaptchaTimeoutError(CaptchaError):
    pass


def _flatten_args(exc):
    """SDK punya bug: exceptions-nya `pass` (enggak manggil super), jadi
    str(exc) bisa kosong padahal pesan aslinya nangkring di .args. Flatten
    biar dapat pesan yang asli, sekalian kebawah kalau args-nya exception lain.
    Kalau nested exception-nya juga kosong, fallback ke nama kelasnya
    (mis. ConnectTimeout) biar pesan error-nya tetap kebaca."""
    buf = []
    for a in getattr(exc, "args", ()):
        if isinstance(a, BaseException):
            inner = _flatten_args(a)
            buf.append(inner or a.__class__.__name__)
        else:
            s = str(a)
            if s and s != "None":
                buf.append(s)
    return " ".join(buf).strip()


def _friendly(exc):
    """Bungkus exception SDK jadi CaptchaError dengan pesan yang lebih enak."""
    msg = _flatten_args(exc)

    if isinstance(exc, TimeoutException):
        return CaptchaTimeoutError(f"2Captcha: {msg or 'timeout'}")

    if any(k in msg for k in ("Connect", "Timeout", "Refused", "CERTIFICATE",
                              "Network", "getaddrinfo", "proxy")):
        msg = "koneksi ke server 2Captcha gagal/putus (jaringan atau blokir)."

    for code, friendly in _FRIENDLY_ERRORS.items():
        if code in msg:
            return CaptchaError(f"2Captcha: {friendly} ({code})")

    return CaptchaError(f"2Captcha: {msg or exc.__class__.__name__}")


def _server_from_host():
    """CAPTCHA_HOST (default https://api.2captcha.com) -> host yang dipakai SDK
    ('api.2captcha.com'). SDK nempel sendiri 'https://' + host + /in.php."""
    host = CAPTCHA_HOST.rstrip("/")
    if "://" in host:
        host = host.split("://", 1)[1]
    return host.rstrip("/")


_solver = None


def get_solver():
    """Singleton AsyncTwoCaptcha sesuai config (.env)."""
    global _solver
    if _solver is None:
        _solver = AsyncTwoCaptcha(
            apiKey=CAPTCHA_API_KEY,
            server=_server_from_host(),
            defaultTimeout=CAPTCHA_TIMEOUT,
            pollingInterval=max(5, int(CAPTCHA_POLL_INTERVAL)),
        )
    return _solver


def captcha_configured():
    """True kalau API key 2Captcha sudah di-set di .env."""
    return bool(CAPTCHA_API_KEY)


# SDK bisa melempar exception dari DUA keluarga (api.* / solver.*), gabungin aja.
_SDK_EXCEPTIONS = (
    ApiException,
    NetworkException,
    TimeoutException,
    ValidationException,
    _sdk_api_exc.ApiException,
    _sdk_api_exc.NetworkException,
)


def sandbox_active():
    """True kalau CAPTCHA_SANDBOX=1 di .env.

    Penanda sisi klien aja: sandbox mode aslinya per-account (diaktifkan di
    https://2captcha.com/setting#sandbox). Kalau nggak diaktifin di dashboard,
    task tetap nyebrang ke worker beneran dan ngecharge saldo.
    """
    return bool(CAPTCHA_SANDBOX)


def _require_configured():
    if not captcha_configured():
        raise CaptchaUnconfiguredError("CAPTCHA_API_KEY belum di-set di .env")


async def get_balance():
    """Cek saldo. Return float (USD). Kalau key belum di-set → 0.0 (bukan error),
    biar path tes bisa jalan tanpa ngeblock."""
    if not captcha_configured():
        return 0.0
    try:
        return await get_solver().balance()
    except _SDK_EXCEPTIONS as e:
        raise _friendly(e) from e


async def solve_turnstile(sitekey, pageurl, action=None, data=None, pagedata=None,
                          user_agent=None, timeout=None):
    """Selesaikan Cloudflare Turnstile (standalone captcha ataupun challenge page).

    Return dict {'token': str, 'captchaId': str}.
    sitekey  : data-sitekey dari elemen Turnstile di halaman.
    pageurl  : URL halaman tempat Turnstile muncul.
    """
    _require_configured()

    kwargs = {}
    if action:
        kwargs["action"] = action
    if data:
        kwargs["data"] = data
    if pagedata:
        kwargs["pagedata"] = pagedata
    if user_agent:
        kwargs["useragent"] = user_agent

    try:
        result = await get_solver().turnstile(
            sitekey=sitekey, url=pageurl, **kwargs
        )
    except _SDK_EXCEPTIONS as e:
        raise _friendly(e) from e

    code = (result or {}).get("code")
    if not code:
        raise CaptchaError(f"2Captcha balikin solusi tanpa token: {result}")
    log.info("Turnstile solved (sitekey %s, captchaId %s)",
             sitekey[:12], (result or {}).get("captchaId"))
    return {
        "token": code,
        "captchaId": (result or {}).get("captchaId"),
    }


async def solve_recaptcha_v2(sitekey, pageurl, invisible=True, timeout=None):
    """Selesaikan reCAPTCHA v2. Return dict {'gRecaptchaResponse': token}."""
    _require_configured()

    try:
        result = await get_solver().recaptcha(
            sitekey=sitekey,
            url=pageurl,
            version="v2",
            invisible=1 if invisible else 0,
        )
    except _SDK_EXCEPTIONS as e:
        raise _friendly(e) from e

    code = (result or {}).get("code")
    if not code:
        raise CaptchaError(f"2Captcha balikin solusi tanpa token: {result}")
    log.info("reCAPTCHA v2 solved (sitekey %s, captchaId %s)",
             sitekey[:12], (result or {}).get("captchaId"))
    return {
        "gRecaptchaResponse": code,
        "captchaId": (result or {}).get("captchaId"),
    }


async def report(task_id, correct=True):
    """Kirim feedback solusi (reportCorrect / reportIncorrect). Best-effort."""
    try:
        await get_solver().report(task_id, correct)
    except Exception as e:  # report gagal nggak fatal
        log.warning("report %s gagal: %s", task_id, e)