#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Login 1fichier pakai kredensial, simpan cookies ke fichier_login_cookies.json
(format Playwright addCookies), lalu tes: halaman login-pl terdeteksi logged-in.

Usage: python fichier_login.py <email> <password>
"""
import json
import re
import sys

import requests

OUT = "/root/botmirror/fichier_login_cookies.json"
BASE = "https://1fichier.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def main():
    if len(sys.argv) < 3:
        print("usage: fichier_login.py <email> <password>")
        return 2
    mail, pw = sys.argv[1], sys.argv[2]

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": f"{BASE}/login.pl"})

    r = s.get(f"{BASE}/login.pl", timeout=25)
    if r.status_code != 200:
        print("login page nggak kebuka:", r.status_code)
        return 1
    # butuh basepage/token? form cuma mail+pass+valider, tapi beberapa versi ada hidden.
    data = {"mail": mail, "pass": pw, "valider": "OK"}
    m = re.search(r'name="([a-z_]+)" value="([^"]*)"', r.text)
    r2 = s.post(f"{BASE}/login.pl", data=data, timeout=25)
    txt = r2.text
    logged = bool(re.search(r"/logout|logout\.pl|console/", txt, re.I)) and "Wrong" not in txt and "Incorrect" not in txt
    if not logged:
        err = re.search(r'class="notice[^>]*>(.{0,120})', txt, re.DOTALL)
        print("LOGIN GAGAL:", (err.group(1).strip() if err else "lihat response"))
        return 1

    cookies = []
    for c in s.cookies:
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": (c.domain or ".1fichier.com").lstrip("."),
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": ("httponly" in str(c._rest).lower()) if hasattr(c, "_rest") else True,
        })
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    print("LOGIN OK -> cookies disimpan:", OUT, "| count:", len(cookies))
    return 0


if __name__ == "__main__":
    sys.exit(main())