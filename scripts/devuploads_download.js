// Usage: node devuploads_download.js <url> <work_dir>
// Output: JSON -> {"ok": true, "direct_url": "...", "filename": "...", "referer": "..."}
//               atau {"ok": false, "error": "..."}
//
// Devuploads (PPD host) alurnya bukan direct-click, tapi multi-step HTTP:
//   1. GET halaman file  -> ambil semua <input name=...> (devpages/rand/dll)
//   2. POST data itu ke safelink sponsor (mis. gujjukhabar.in) dan ambil
//      input baru dari respons -> dapat form "download2" (op, id, rand, xd...)
//   3. GET du2.devuploads.com/dlhash.php dengan Origin/Referer safelink
//      -> token "ipp"
//   4. POST devuploads.com/token/token.php dengan {rand, msg} dan header
//      safelink -> token "xd"
//   5. POST ulang halaman file dengan data gabungan (download2 + ipp + xd)
//      -> respons berisi <input name="orilink" value="<direct URL>">
//  (pola dari mirror-leech-telegram-bot yang terbukti bekerja)

const path = require("path");
const fs = require("fs");

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0";

// cookie jar sederhana (Map domain -> Map name:value)
const cookieJar = new Map();

function jarGet(url) {
  const host = new URL(url).hostname;
  const parts = [];
  for (const [domain, cookies] of cookieJar) {
    if (host === domain || host.endsWith("." + domain)) {
      for (const [k, v] of cookies) parts.push(`${k}=${v}`);
    }
  }
  return { Cookie: parts.join("; ") };
}

function jarSet(url, setCookies) {
  if (!setCookies) return;
  const host = new URL(url).hostname;
  for (const line of setCookies) {
    const m = line.match(/^([^=;\s]+)=([^;]*)/);
    if (!m) continue;
    const name = m[1];
    const value = m[2];
    let domain = host;
    const dm = line.match(/domain=([^;\s]+)/i);
    if (dm) {
      domain = dm[1].replace(/^\./, "");
    }
    if (!cookieJar.has(domain)) cookieJar.set(domain, new Map());
    cookieJar.get(domain).set(name, value);
  }
}

async function req(url, { method = "GET", headers = {}, body } = {}) {
  const h = {
    "User-Agent": UA,
    Accept:
      "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif," +
      "image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    ...jarGet(url),
    ...headers,
  };
  if (body) {
    h["Content-Type"] = "application/x-www-form-urlencoded";
    h["Content-Length"] = String(body.length);
  }
  const res = await fetch(url, { method, headers: h, body, redirect: "follow" });
  jarSet(res.url || url, res.headers.getSetCookie ? res.headers.getSetCookie() : []);
  const text = await res.text();
  return { status: res.status, url: res.url || url, text };
}

function parseInputs(html) {
  const out = [];
  const re = /<input\b[^>]*>/gi;
  let m;
  while ((m = re.exec(html))) {
    const tag = m[0];
    const n = (tag.match(/name="([^"]+)"/) || [])[1];
    if (!n) continue;
    const v = (tag.match(/value="([^"]*)"/) || [])[1] || "";
    out.push({ name: n, value: v });
  }
  return out;
}

function formEncode(entries) {
  const sp = new URLSearchParams();
  for (const e of entries) sp.set(e.name, e.value);
  return sp.toString();
}

function extractFilename(html, fallback) {
  const m = html.match(/var filename\s*=\s*'([^']+)'/);
  if (m) return m[1];
  const t = html.match(/<input\b[^>]*name="title"[^>]*value="([^"]+)"/i);
  return t ? t[1] : fallback;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node devuploads_download.js <url> <work_dir>" }));
    process.exit(1);
  }
  try {
    // [1] halaman file -> input pertama
    const p1 = await req(url);
    if (p1.status !== 200) throw new Error(`GET file page gagal (HTTP ${p1.status})`);
    const filename = extractFilename(p1.text, url.split("/").pop() || "downloaded_file");
    let data = parseInputs(p1.text);
    if (!data.length) throw new Error("Tidak ada data link (file expired / salah URL).");
    // pastikan ransite ikut (default 100)
    if (!data.find((e) => e.name === "ransite")) data.push({ name: "ransite", value: "100" });

    // [2] POST ke safelink sponsor -> dapat form "download2"
    // pilihan safelink meniru genLinks() halaman (id 3 untuk .apk -> gujjukhabar.in;
    // id 2 pdf/epub/m4b/mp3 -> smartfeecalculator; default id 4 -> pdfhindibook;
    // id 1 -> djxmaza bila block). Pakai root domain sebagai endpoint.
    const base = url.replace(/^https?:\/\//, "").split("/")[0];
    const low = filename.toLowerCase();
    let safelinks = [];
    if (low.endsWith(".apk") || low.endsWith(".xapk")) {
      safelinks = ["https://gujjukhabar.in/"];
    } else if (/(\.pdf|\.epub|\.m4b|\.mp3)$/.test(low)) {
      safelinks = ["https://smartfeecalculator.com/"];
    } else {
      safelinks = ["https://pdfhindibook.com/", "https://gujjukhabar.in/", "https://smartfeecalculator.com/"];
    }
    const saferef = "https://" + new URL(safelinks[0]).hostname;

    let d2 = null;
    let safelink = null;
    for (const s of safelinks) {
      try {
        const r = await req(s, {
          method: "POST",
          headers: { Origin: `https://${base}`, Referer: url },
          body: formEncode(data),
        });
        const inputs = parseInputs(r.text);
        if (inputs.find((e) => e.name === "id")) {
          d2 = inputs;
          safelink = `https://${new URL(r.url || s).hostname}`;
          break;
        }
      } catch (_) {}
    }
    if (!d2) throw new Error("Safelink sponsor tidak merespon form download.");

    // [3] dlhash.php -> ipp
    const dlh = await req(`https://du2.devuploads.com/dlhash.php`, {
      headers: { Origin: safelink, Referer: safelink + "/" },
    });
    const ipp = (dlh.text || "").trim();
    if (!ipp) throw new Error("Gagal mendapatkan token ipp (dlhash.php).");

    // [4] token.php -> xd
    const rand = (d2.find((e) => e.name === "rand") || {}).value || "";
    const tk = await req(`https://devuploads.com/token/token.php`, {
      method: "POST",
      headers: { Origin: safelink, Referer: safelink + "/" },
      body: formEncode([{ name: "rand", value: rand }, { name: "msg", value: "" }]),
    });
    const xd = (tk.text || "").trim();
    if (!xd) throw new Error("Gagal mendapatkan token xd (token.php).");

    // [5] POST final ke halaman file dengan data download2 + ipp + xd
    const final = d2.map((e) => ({ ...e }));
    const ix = final.find((e) => e.name === "ipp");
    if (ix) ix.value = ipp; else final.push({ name: "ipp", value: ipp });
    const xdx = final.find((e) => e.name === "xd");
    if (xdx) xdx.value = xd; else final.push({ name: "xd", value: xd });

    const fin = await req(url, {
      method: "POST",
      headers: { Origin: safelink, Referer: safelink + "/" },
      body: formEncode(final),
    });
    const ori = (fin.text.match(/<input\b[^>]*name="orilink"[^>]*value="([^"]+)"/i) || [])[1];
    if (!ori) {
      if (/File Not Found|file no longer|expired/i.test(fin.text)) {
        throw new Error("File tidak ditemukan / link expired.");
      }
      const crafty = fs.existsSync(workDir) ? "" : "";
      const shotPath = path.join(workDir, "devuploads_debug.html");
      if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });
      fs.writeFileSync(shotPath, fin.text.slice(0, 8000));
      throw new Error(`Tidak ketemu orilink. Debug: ${shotPath}`);
    }

    const direct = ori.replace(/&amp;/g, "&");
    console.log(
      JSON.stringify({
        ok: true,
        direct_url: direct,
        filename,
        referer: `https://${base}`,
      })
    );
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String((err && err.message) || err) }));
    process.exitCode = 1;
  }
}

main();