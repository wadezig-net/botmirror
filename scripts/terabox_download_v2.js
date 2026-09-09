// Usage: node terabox_download.js <url> <work_dir>
// Output: JSON satu baris ->
//   {"ok": true, "type": "single", "direct_url": "...", "filename": "...", "cookie_file": "...", "referer": "..."}
//   {"ok": true, "type": "folder", "files": [{"url","filename"}...], ...}
//   {"ok": false, "error": "..."}

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

// Versi Terabox selalu berubah - konfigurasi di sini biar gampang update
const CONFIG = {
  UA: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  // API endpoint - bisa berubah, monitor respon server
  API_BASE: "https://www.terabox.com",
  API_PARAMS: {
    app_id: "250528",
    channel: "chunlei",
    web: "1",
    clienttype: "0",
  },
  // Timeout dalam ms
  TIMEOUTS: {
    goto: 60000,
    apiCall: 30000,
    domReady: 5000,
  },
  // Delay setelah goto sebelum extract cookie (biar JS jalan dulu)
  POST_GOTO_DELAY: 3000,
  // Delay tambahan kalau cookie kosong
  COOKIE_RETRY_DELAY: 5000,
  // Rekursi folder max depth
  MAX_RECURSIVE_DEPTH: 10,
};

function log(...args) {
  console.error("[debug]", new Date().toISOString(), ...args);
}

/**
 * Ekstrak surl dari berbagai format URL Terabox
 * Format yang didukung:
 * - https://www.terabox.com/share/link?surl=xxx
 * - https://www.terabox.com/s/xxx
 * - https://www.1024tera.com/s/xxx
 * - https://www.terabox.com/sharing/link?k=xxx
 * - dll
 */
function surlFromUrl(url) {
  // Pola 1: /surl= atau /s/
  let m = url.match(/[?&]surl=([^&]+)/) || url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  
  // Pola 2: sometimes surl in path setelah /sharing/
  m = url.match(/\/sharing\/link[?#]?[?&]?surl=([^&]+)/) || url.match(/\/sharing\/link\/([^/?]+)/);
  if (m) return m[1];
  
  // Pola 3: kadang surl setelah kode acak di path
  m = url.match(/\/([a-zA-Z0-9]{6,32})\/?$/);
  
  return null;
}

/**
 * Build query string untuk API call
 */
function buildApiParams(extraParams) {
  const params = new URLSearchParams(CONFIG.API_PARAMS);
  for (const [key, value] of Object.entries(extraParams)) {
    params.set(key, value);
  }
  return params.toString();
}

/**
 * HTTP GET ke API Terabox dengan headers lengkap
 */
async function apiGet(apiPath, cookies, refererUrl, extraParams = {}) {
  const qs = buildApiParams(extraParams);
  const url = `${CONFIG.API_BASE}${apiPath}?${qs}`;
  
  const startTime = Date.now();
  log("API call:", apiPath, "params:", extraParams);
  
  try {
    const resp = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": CONFIG.UA,
        "Referer": refererUrl,
        "Cookie": cookies,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
      },
      signal: AbortSignal.timeout(CONFIG.TIMEOUTS.apiCall),
    });
    
    const elapsed = Date.now() - startTime;
    log("API response:", resp.status, "in", elapsed, "ms");
    
    const text = await resp.text();
    
    // Log headers yang menarik untuk anti-bot
    const interestingHeaders = ["yld", "yme", "logid", "flow-level"];
    const headerInfo = {};
    for (const h of interestingHeaders) {
      const val = resp.headers.get(h);
      if (val) headerInfo[h] = val.slice(0, 100);
    }
    if (Object.keys(headerInfo).length > 0) {
      log("Headers:", headerInfo);
    }
    
    try {
      const json = JSON.parse(text);
      log("API JSON:", JSON.stringify(json).slice(0, 500));
      return { json, raw: text, status: resp.status };
    } catch (_) {
      log("API raw response (not JSON):", text.slice(0, 500));
      return { json: null, raw: text, status: resp.status };
    }
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("API call timeout setelah " + (CONFIG.TIMEOUTS.apiCall / 1000) + " detik");
    }
    throw err;
  }
}

/**
 * Cek apakah response API menandakan butuh login/verifikasi
 */
function isProtectedError(json) {
  if (!json || typeof json !== "object") return false;
  const errno = json.errno;
  // Error codes yang menandakan proteksi
  const protectedCodes = [400310, -6, 103, 104, 107, 109];
  return protectedCodes.includes(errno) || 
         (json.errmsg && /login|verify|captcha|password/i.test(json.errmsg));
}

/**
 * Extract dlink dari item list - beberapa format punya field berbeda
 */
function extractDlink(item) {
  if (!item || typeof item !== "object") return null;
  
  // Field langsung
  if (item.dlink && typeof item.dlink === "string" && item.dlink.startsWith("http")) {
    return item.dlink;
  }
  if (item.dlink_url && typeof item.dlink_url === "string") {
    return item.dlink_url;
  }
  if (item.download_url && typeof item.download_url === "string") {
    return item.download_url;
  }
  
  // Kadang dlink di nested object
  if (item.data && item.data.dlink) return item.data.dlink;
  
  return null;
}

/**
 * Jika dlink tidak ada di list, coba endpoint /share/download
 */
async function getDlinkViaDownloadEndpoint(apiPath, cookies, refererUrl, surl, fsid) {
  log("Mencoba ambil dlink via /share/download...");
  
  const result = await apiGet(apiPath, cookies, refererUrl, {
    shorturl: surl,
    fsid: String(fsid),
    rt: "1",
    type: "dlink",
  });
  
  if (result.json && result.json.errno === 0 && result.json.dlink) {
    log("Dlink didapat via download endpoint");
    return result.json.dlink;
  }
  
  // Coba format lain - kadang field-nya beda
  if (result.json && result.json.errno === 0) {
    if (result.json.data && result.json.data.dlink) {
      return result.json.data.dlink;
    }
    if (result.json.url) return result.json.url;
  }
  
  log("Gagal ambil dlink via download endpoint, errno:", result.json?.errno);
  return null;
}

/**
 * Coba dapatkan dlink dari halaman HTML langsung (tanpa API)
 * Kadang Terabox embed link di halaman untuk user yang sudah login
 */
async function tryExtractDlinkFromPage(page) {
  log("Mencoba ekstrak dlink dari halaman HTML...");
  
  try {
    // Tunggu sebentar biar JS render selesai
    await page.waitForTimeout(2000);
    
    // Cek di source HTML
    const content = await page.content();
    
    // Pola 1: link download di attribute
    const patterns = [
      /"dlink"\s*:\s*"([^"]+)"/i,
      /data-dlink="([^"]+)"/i,
      /dlink_url\s*=\s*"([^"]+)"/i,
      /https:\/\/[^"']*\.terabox\.com\/[^"?]*\?[^"]*dlink[^"]*"/gi,
      /https:\/\/[a-z0-9.-]*\.yek\.com\/t\/[a-zA-Z0-9]+/gi,
    ];
    
    for (const pattern of patterns) {
      const match = content.match(pattern);
      if (match) {
        log("Dlink ditemukan di HTML:", match[0].slice(0, 100));
        return match[1] || match[0];
      }
    }
    
    // Pola 2: cek di console log atau variable JS
    const jsVars = await page.evaluate(() => {
      const results = [];
      // Cek window variables
      for (const key of Object.keys(window)) {
        const val = window[key];
        if (val && typeof val === "object" && val.dlink) {
          results.push(val.dlink);
        }
      }
      return results;
    });
    
    if (jsVars.length > 0) {
      log("Dlink ditemukan di JS variable:", jsVars[0].slice(0, 100));
      return jsVars[0];
    }
    
    log("Tidak ada dlink di halaman HTML");
    return null;
  } catch (err) {
    log("Error ekstrak dlink dari halaman:", err.message);
    return null;
  }
}

/**
 * Handle kasus folder - rekursi dan resolve semua file
 */
async function processFolder(rootJson, cookies, refererUrl, surl, apiPath, workDir) {
  const rootList = rootJson.list || [];
  
  // Kumpulkan semua file (rekursi)
  const allFiles = [];
  
  async function walk(dirPath, depth) {
    if (depth > CONFIG.MAX_RECURSIVE_DEPTH) {
      log("Melebihi max depth, berhenti rekursi");
      return;
    }
    
    const res = await apiGet(apiPath, cookies, refererUrl, {
      shorturl: surl,
      root: "0",
      dir: dirPath,
    });
    
    if (!res.json || !res.json.list) {
      log("Gagal list folder:", dirPath, res.json?.errno);
      return;
    }
    
    const items = res.json.list;
    for (const item of items) {
      if (String(item.isdir) === "1") {
        await walk(item.path, depth + 1);
      } else {
        allFiles.push({
          name: item.server_filename || item.name || "unknown",
          fsid: item.fs_id || item.fsid,
          path: item.path,
          size: item.size,
          dlink: extractDlink(item),
        });
      }
    }
  }
  
  log("Memulai rekursi folder...");
  await walk("/BARAT", 0);
  
  log(`Total file ditemukan: ${allFiles.length}`);
  
  // Resolve dlink untuk tiap file yang belum punya
  const filesWithDlink = [];
  let resolvedCount = 0;
  let failedCount = 0;
  
  for (const file of allFiles) {
    if (file.dlink) {
      filesWithDlink.push({ url: file.dlink, filename: file.name });
      resolvedCount++;
    } else {
      // Coba ambil dlink via endpoint
      const dlink = await getDlinkViaDownloadEndpoint(
        apiPath, cookies, refererUrl, surl, file.fsid
      );
      if (dlink) {
        filesWithDlink.push({ url: dlink, filename: file.name });
        resolvedCount++;
      } else {
        failedCount++;
        log("Gagal resolve dlink untuk:", file.name);
      }
    }
  }
  
  return { files: filesWithDlink, total: allFiles.length, failed: failedCount };
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
    log("Start: membuka browser...");
    browser = await chromium.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-gpu",
      ],
    });
    
    const context = await browser.newContext({
      acceptDownloads: true,
      userAgent: CONFIG.UA,
      locale: "en-US",
      viewport: { width: 1920, height: 1080 },
    });
    
    // Sembunyikan tanda automation
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
      Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3] });
      Object.defineProperty(navigator, "languages", { get: () => ["en-US", "en"] });
    });
    
    const page = await context.newPage();
    page.on("dialog", async (d) => d.dismiss().catch(() => {}));
    
    log("Navigasi ke URL:", url);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: CONFIG.TIMEOUTS.goto }).catch((e) => log("goto error:", e.message));
    
    // Tunggu DOM siap sepenuhnya
    await page.waitForTimeout(CONFIG.POST_GOTO_DELAY);
    
    // Dapatkan cookies
    let cookies = await context.cookies();
    log("Cookie count:", cookies.length);
    
    // Jika tidak ada cookies, mungkin blocked - retry
    if (cookies.length === 0) {
      log("Tidak ada cookie, tunggu tambahan...");
      await page.waitForTimeout(CONFIG.COOKIE_RETRY_DELAY);
      cookies = await context.cookies();
      if (cookies.length === 0) {
        throw new Error("Terabox tidak memberikan cookie session. Kemungkinan IP terblokir atau server sedang maintenance.");
      }
    }
    
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join("; ");
    
    // Ekstrak surl
    const surl = surlFromUrl(page.url() && /surl=/i.test(page.url()) ? page.url() : url) || surlFromUrl(url);
    if (!surl) {
      throw new Error("Tidak bisa ekstrak kode surl dari link Terabox. Format link tidak dikenali.");
    }
    
    log("Surl extracted:", surl);
    
    const refererUrl = `https://www.terabox.com/sharing/link?surl=${surl}`;
    
    // ---- Langkah 1: list isi share ----
    const root = await apiGet("/share/list", cookieHeader, refererUrl, {
      shorturl: surl,
      root: "1",
    });
    
    if (root.json && root.json.errno !== undefined && root.json.errno !== 0) {
      const codes = {
        "105": "link tidak ditemukan / expired",
        "-6": "butuh login (link privat)",
        "-7": "link tidak valid",
        "103": "akses ditolak",
        "104": "parameter tidak valid",
        "107": "resource tidak ada",
        "109": "operasi tidak diizinkan",
      };
      
      // Kalau error 105 tapi kita baru buka halaman, mungkin surl salah
      if (root.json.errno === 105) {
        // Coba ekstrak surl ulang dari URL halaman
        const pageSurl = surlFromUrl(page.url());
        if (pageSurl && pageSurl !== surl) {
          log("Surl berubah setelah navigasi, coba lagi dengan surl baru:", pageSurl);
          const retry = await apiGet("/share/list", cookieHeader, refererUrl, {
            shorturl: pageSurl,
            root: "1",
          });
          if (retry.json && retry.json.errno === 0) {
            // Gunakan surl yang baru
            root.json = retry.json;
            root.raw = retry.raw;
          }
        }
      }
      
      if (root.json.errno !== 0) {
        throw new Error(`Terabox API error ${root.json.errno}${root.json.errmsg ? ": " + root.json.errmsg : ""}. ${codes[root.json.errno] ? `(${codes[root.json.errno]})` : ""}`);
      }
    }
    
    if (root.json && root.json.errno === undefined) {
      throw new Error("Respons API Terabox tak dikenali: " + String(root.raw || "").slice(0, 200));
    }
    
    const rootList = root.json.list || [];
    log("Jumlah item di root:", rootList.length);
    
    // ---- Cek apakah single file ----
    if (rootList.length === 1 && String(rootList[0].isdir) !== "1") {
      const item = rootList[0];
      log("Single file detected:", item.server_filename || item.name);
      
      // Coba dlink dari list dulu
      let dlink = extractDlink(item);
      
      if (!dlink) {
        // Coba endpoint download
        dlink = await getDlinkViaDownloadEndpoint(
          "/share/download", cookieHeader, refererUrl, surl, item.fs_id
        );
      }
      
      // Kalau masih belum ada, coba dari halaman HTML
      if (!dlink) {
        dlink = await tryExtractDlinkFromPage(page);
      }
      
      if (!dlink) {
        throw new Error(
          "File Terabox ini ter-proteksi (butuh login/verify). " +
          "Coba buka link di browser, masuk akun Terabox, lalu klik Download."
        );
      }
      
      const cookieFile = path.join(workDir, "terabox_cookies.json");
      fs.writeFileSync(cookieFile, JSON.stringify(cookies));
      
      console.log(JSON.stringify({
        ok: true,
        type: "single",
        direct_url: dlink,
        filename: item.server_filename || item.name || "terabox_file",
        cookie_file: cookieFile,
        referer: refererUrl,
      }));
      return;
    }
    
    // ---- Folder: process semua file ----
    log("Folder detected, memproses...");
    
    const result = await processFolder(root.json, cookieHeader, refererUrl, surl, "/share/list", workDir);
    
    if (result.files.length === 0) {
      throw new Error(
        `Folder berisi ${result.total} file, tapi semua dlink-nya gagal di-resolve. ` +
        `Kemungkinan besar butuh login/verify_v2. Coba buka link di browser dan login terlebih dahulu.`
      );
    }
    
    const cookieFile = path.join(workDir, "terabox_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    
    console.log(JSON.stringify({
      ok: true,
      type: "folder",
      files: result.files,
      total: result.total,
      failed: result.failed,
      cookie_file: cookieFile,
      referer: refererUrl,
    }));
    
  } catch (err) {
    log("ERROR:", err.message);
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
}

main();
