// Usage: node terabox_download.js <url> <work_dir>
// Output: JSON satu baris ->
//   {"ok": true, "type": "single", "direct_url": "...", "filename": "...", "cookie_file": "...", "referer": "..."}
//   {"ok": true, "type": "folder", "files": [{"url","filename"}...], ...}
//   {"ok": false, "error": "..."}
//
// Terabox (Baidu) sejak ~2024 menutup jalur API lama dengan anti-bot "verify_v2".
// Strategi yang dipakai di sini (dan yang dipakai banyak tool yang masih jalan):
//   1. Buka halaman share sekali di headless browser supaya dapat cookie session
//      (BDCLND/ts dkk) + untuk melewati basic bot-detection.
//   2. Dari sisi Node (BUKAN page.evaluate, biar nggak kena CSP), panggil API
//      publik /share/list?app_id=250528&shorturl=...&root=1. Untuk share file
//      tunggal yang "ramah", entity-nya langsung menyertakan field `dlink`.
//   3. Kalau berupa folder (isdir=1), rekursi mulai dari root dan tampilkan
//      jumlah file; per file dicoba di-resolve dlink-nya. Kalau API minta
//      verify_v2/login, beri pesan jelas (link ter-proteksi).
//   4. dlink Terabox cepat expired & sekali pakai, jadi download ke file
//      langsung di sisi Python tanpa fetch ulang.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function surlFromUrl(url) {
  const m = url.match(/\/s\/([^/?]+)/) || url.match(/[?&]surl=([^&]+)/);
  return m ? m[1] : null;
}

async function apiGet(apiPath, cookies, refererUrl, params) {
  const qs = new URLSearchParams({
    app_id: "250528",
    channel: "chunlei",
    web: "1",
    clienttype: "0",
    ...params,
  });
  const url = `https://www.terabox.com${apiPath}?${qs.toString()}`;
  const resp = await fetch(url, {
    headers: {
      "User-Agent": UA,
      "Referer": refererUrl,
      "Cookie": cookies,
    },
  });
  const text = await resp.text();
  try {
    return { json: JSON.parse(text) };
  } catch (_) {
    return { json: null, raw: text };
  }
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node terabox_download.js <url> <work_dir>" }));
    process.exit(1);
  }
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });

  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
    });
    const context = await browser.newContext({
      acceptDownloads: true,
      userAgent: UA,
      locale: "en-US",
    });
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });

    const page = await context.newPage();
    page.on("dialog", async (d) => d.dismiss().catch(() => {}));

    log("navigasi ke:", url);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));
    await page.waitForTimeout(3000);

    const cookies = await context.cookies();
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const surl = surlFromUrl(page.url() && /surl=/i.test(page.url()) ? page.url() : url) || surlFromUrl(url);
    if (!surl) {
      throw new Error("Tidak bisa ekstrak kode surl dari link Terabox.");
    }

    // pastikan kita punya cookie session; kalau nggak ada sama sekali, kemungkinan
    // halaman nge-block (Cloudflare/region) -- coba sekali lagi tunggu 5 detik.
    if (cookies.length === 0) {
      await page.waitForTimeout(5000);
      const c2 = await context.cookies();
      if (c2.length === 0) {
        throw new Error("Terabox nggak memberi cookie session (kemungkinan terblokir IP/region).");
      }
    }

    const refererUrl = `https://www.terabox.com/sharing/link?surl=${surl}`;

    // ---- langkah 1: list isi share (root) ----
    const root = await apiGet("/share/list", cookieHeader, refererUrl, {
      shorturl: surl,
      root: "1",
    });

    if (root.json && root.json.errno !== undefined && root.json.errno !== 0) {
      const codes = { "105": "link tidak ditemukan / expired", "-6": "butuh login (link privat)", "-7": "link tidak valid" };
      throw new Error(`Terabox API error ${root.json.errno}${root.json.errmsg ? ": " + root.json.errmsg : ""}. ` +
        (codes[root.json.errno] ? `(${codes[root.json.errno]})` : ""));
    }

    const rootList = (root.json && root.json.list) || [];

    if (root.json && root.json.errno === undefined) {
      throw new Error("Respons API Terabox tak dikenali: " + String(root.raw || "").slice(0, 200));
    }

    // ---- langkah 2: coba ambil dlink langsung (single-file friendly) ----
    function firstDlink(list) {
      for (const item of list) {
        if (item && typeof item.dlink === "string" && item.dlink.startsWith("http")) {
          return item;
        }
      }
      return null;
    }

    if (rootList.length === 1 && String(rootList[0].isdir) !== "1") {
      // single file: kalau dlink belum ada di list, coba endpoint download singkat
      let item = firstDlink(rootList);
      if (!item) {
        const dlRes = await apiGet("/share/download", cookieHeader, refererUrl, {
          shorturl: surl,
          fsid: String(rootList[0].fs_id),
          rt: "1",
          type: "dlink",
        });
        if (dlRes.json && dlRes.json.errno === 0 && dlRes.json.dlink) {
          item = { dlink: dlRes.json.dlink, server_filename: rootList[0].server_filename };
        } else {
          const errno = dlRes.json && dlRes.json.errno;
          const blocked = errno === 400310 || errno === -6;
          throw new Error(
            "File single Terabox ini ter-proteksi (Butuh login/verify). " +
            "User bisa membuka link di HP, masuk akun, lalu klik Download otomatis." +
            (dlRes.json && dlRes.json.errmsg ? ` [${errno}: ${dlRes.json.errmsg}]` : "")
          );
        }
      }
      const cookieFile = path.join(workDir, "terabox_cookies.json");
      fs.writeFileSync(cookieFile, JSON.stringify(cookies));
      console.log(JSON.stringify({
        ok: true,
        type: "single",
        direct_url: item.dlink,
        filename: item.server_filename || item.server_filename || "terabox_file",
        cookie_file: cookieFile,
        referer: refererUrl,
      }));
      return;
    }

    // ---- langkah 3: folder -> rekursi ----
    const collected = [];
    async function walk(dirPath) {
      const res = await apiGet("/share/list", cookieHeader, refererUrl, {
        shorturl: surl,
        root: "0",
        dir: dirPath,
      });
      const items = (res.json && res.json.list) || [];
      for (const item of items) {
        if (String(item.isdir) === "1") {
          try { await walk(item.path); } catch (_) {}
        } else {
          collected.push({
            name: item.server_filename,
            fsid: String(item.fs_id),
            path: item.path,
            size: item.size,
          });
        }
      }
    }

    log("share berupa folder; memetakan isi");
    await walk("/BARAT");

    if (collected.length === 0) {
      throw new Error("Folder Terabox kosong / butuh login untuk diakses.");
    }

    // coba resolve dlink untuk tiap file; kalau semua ngeblok, lapor dengan jelas.
    const files = [];
    let blockedCount = 0;
    for (const f of collected) {
      const dlRes = await apiGet("/share/download", cookieHeader, refererUrl, {
        shorturl: surl,
        fsid: f.fsid,
        rt: "1",
        type: "dlink",
      });
      if (dlRes.json && dlRes.json.errno === 0 && dlRes.json.dlink) {
        files.push({ url: dlRes.json.dlink, filename: f.name });
        log("dlink OK:", f.name.slice(0, 60));
      } else {
        blockedCount++;
      }
    }

    if (files.length === 0) {
      throw new Error(
        `Folder berisi ${collected.length} file, tapi semua dlink-nya terblokir anti-bot ` +
        "(butuh login/verify_v2). Terabox semakin ketat -- coba link file tunggal, " +
        "atau siapkan cookie akun sebagai fallback."
      );
    }

    const cookieFile = path.join(workDir, "terabox_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    console.log(JSON.stringify({
      ok: true,
      type: "folder",
      files,
      skipped: blockedCount,
      cookie_file: cookieFile,
      referer: refererUrl,
    }));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
}

main();