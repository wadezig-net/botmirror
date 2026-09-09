/**
 * Terabox Download - Hybrid version
 * 
 * Strategy:
 * 1. Gunakan browser headless (Playwright) hanya untuk dapat cookies session
 * 2. Tutup browser after dapat cookies
 * 3. Gunakan fetch biasa untuk API calls (lebih ringan & reliable)
 * 4. Handle semua edge case dan error yang mungkin terjadi
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  // Browser hanya dibuka sesingkat mungkin untuk dapat cookies
  BROWSER_TIMEOUT: 30000,
  POST_NAV_DELAY: 2000, // delay setelah navigasi sebelum ambil cookies
  COOKIE_RETRY_DELAY: 3000, // delay jika cookies kosong
  MAX_RETRIES: 2,
  // Timeout untuk API call
  API_TIMEOUT: 30000,
};

function log(...args) {
  console.error('[terabox]', new Date().toISOString(), ...args);
}

/**
 * Ekstrak surl dari berbagai format URL Terabox
 */
function surlFromUrl(url) {
  // Pola: /surl= atau /s/
  let m = url.match(/[?&]surl=([^&]+)/) || url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  
  // Pola: /sharing/link?surl= atau /sharing/link/
  m = url.match(/\/sharing\/link[?#]?[?&]?surl=([^&]+)/) || url.match(/\/sharing\/link\/([^/?]+)/);
  if (m) return m[1];
  
  // Pola: kode acak di path akhir (panjang 6-32 karakter)
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  
  return null;
}

/**
 * Build cookie header dari array cookies
 */
function cookieHeader(cookies) {
  return cookies.map(c => `${c.name}=${c.value}`).join('; ');
}

/**
 * Fetch dengan headers standar
 */
async function fetchWithHeaders(url, cookies, referer, extraHeaders = {}) {
  const headers = {
    'User-Agent': CONFIG.UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': referer,
    'Cookie': cookieHeader(cookies),
    ...extraHeaders,
  };
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CONFIG.API_TIMEOUT);
  
  try {
    const response = await fetch(url, { 
      headers,
      signal: controller.signal,
    });
    return {
      status: response.status,
      headers: Object.fromEntries(response.headers.entries()),
      body: await response.text(),
    };
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Parse respons API Terabox
 */
function parseApiResponse(text) {
  try {
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

/**
 * Handle error code dari API
 */
function interpretErrorCode(errno) {
  const codes = {
    105: 'Link tidak ditemukan / expired',
    '-6': 'Butuh login (link privat)',
    '-7': 'Link tidak valid',
    103: 'Akses ditolak',
    104: 'Parameter tidak valid',
    107: 'Resource tidak ada',
    109: 'Operasi tidak diizinkan',
    400310: 'Link terproteksi (butuh verify)',
  };
  return codes[errno] || `Error ${errno}`;
}

/**
 * Resolve dlink dari item (single file)
 */
async function resolveSingleFileItem(item, cookies, surl, workDir) {
  const apiBase = 'https://www.terabox.com';
  const referer = `https://www.terabox.com/sharing/link?surl=${surl}`;
  
  // Cek dlink langsung dari item
  let dlink = item.dlink || item.dlink_url || item.download_url;
  
  if (dlink) {
    log('Dlink ditemukan langsung dari item:', dlink.slice(0, 100));
    return dlink;
  }
  
  // Jika tidak ada, coba endpoint /share/download
  log('Mencoba endpoint /share/download...');
  const fsid = item.fs_id || item.fsid;
  
  const downloadParams = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    fsid: String(fsid),
    rt: '1',
    type: 'dlink',
  });
  
  const downloadUrl = `${apiBase}/share/download?${downloadParams}`;
  const result = await fetchWithHeaders(downloadUrl, cookies, referer);
  
  const data = parseApiResponse(result.body);
  if (data && data.errno === 0 && data.dlink) {
    log('Dlink berhasil diambil dari endpoint download');
    return data.dlink;
  }
  
  // Coba response langsung (beberapa versi API return URL langsung)
  if (data && data.url) {
    return data.url;
  }
  
  // Jika masih gagal, coba dengan params berbeda
  const altParams = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    fsid: String(fsid),
    type: 'download',
  });
  
  const altUrl = `${apiBase}/share/download?${altParams}`;
  const altResult = await fetchWithHeaders(altUrl, cookies, referer);
  
  try {
    const altData = JSON.parse(altResult.body);
    if (altData && altData.errno === 0 && altData.dlink) {
      log('Dlink berhasil dari endpoint alternatif');
      return altData.dlink;
    }
  } catch (e) {}
  
  throw new Error(`File single tidak bisa di-resolve (fsid: ${fsid})`);
}

/**
 * Process folder - kumpulkan semua file dengan dlink
 */
async function processFolder(list, cookies, surl, workDir) {
  const apiBase = 'https://www.terabox.com';
  const referer = `https://www.terabox.com/sharing/link?surl=${surl}`;
  const files = [];
  let resolved = 0;
  let failed = 0;
  
  log(`Memproses folder dengan ${list.length} item...`);
  
  for (const item of list) {
    const name = item.server_filename || item.name || 'unknown';
    const fsid = item.fs_id || item.fsid;
    const isDir = String(item.isdir) === '1';
    
    if (isDir) {
      log(`  Folder: ${name} (skip, butuh rekursi)`);
      continue;
    }
    
    log(`  File: ${name}`);
    
    // Cek dlink langsung dari item
    let dlink = item.dlink || item.dlink_url || item.download_url;
    
    if (!dlink) {
      // Coba endpoint /share/download
      // Coba multiple endpoint variasi
      const endpoints = [
        { params: { rt: '1', type: 'dlink' }, desc: 'dlink' },
        { params: { type: 'download' }, desc: 'download' },
        { params: { rt: '1' }, desc: 'rt=1' },
      ];
      
      for (const ep of endpoints) {
        const downloadParams = new URLSearchParams({
          app_id: '250528',
          channel: 'chunlei',
          web: '1',
          clienttype: '0',
          shorturl: surl,
          fsid: String(fsid),
          ...ep.params,
        });
        
        const downloadUrl = `${apiBase}/share/download?${downloadParams}`;
        const result = await fetchWithHeaders(downloadUrl, cookies, referer);
        
        try {
          const data = JSON.parse(result.body);
          if (data && data.errno === 0 && data.dlink) {
            dlink = data.dlink;
            log(`    ✓ Dlink dari endpoint (${ep.desc})`);
            break;
          }
        } catch (e) {}
      }
    }
    
    if (dlink) {
      files.push({ url: dlink, filename: name });
      resolved++;
      log(`    ✓ Dlink didapat`);
    } else {
      failed++;
      log(`    ✗ Gagal resolve dlink`);
    }
  }
  
  log(`Hasil: ${resolved} berhasil, ${failed} gagal`);
  return { files, total: list.length, resolved, failed };
}

/**
 * Main function
 */
async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Usage: node terabox_final.js <url> <work_dir>' 
    }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
  let browser;
  
  try {
    log('Membuka browser headless untuk dapat cookies session...');
    
    // Launch browser dengan konfigurasi minimal
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-blink-features=AutomationControlled',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--single-process',
      ],
      executablePath: process.env.CHROMIUM_PATH,
    });
    
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
      acceptDownloads: false, // kita tidak perlu download dari browser
    });
    
    // Sembunyikan tanda automation
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
      });
    });
    
    const page = await context.newPage();
    
    // Handle dialog/notification
    page.on('dialog', async (d) => d.dismiss().catch(() => {}));
    
    // Navigasi ke halaman share
    log('Navigasi ke:', url);
    await page.goto(url, { 
      waitUntil: 'domcontentloaded', 
      timeout: CONFIG.BROWSER_TIMEOUT 
    }).catch(e => log('Navigate error (mungkin tidak fatal):', e.message));
    
    // Tunggu sebentar untuk JS render
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Dapatkan cookies
    let cookies = await context.cookies();
    log(`Cookie count: ${cookies.length}`);
    
    // Retry jika cookies kosong
    if (cookies.length === 0) {
      log('Cookies kosong, retry...');
      await page.waitForTimeout(CONFIG.COOKIE_RETRY_DELAY);
      cookies = await context.cookies();
      log(`Cookie count after retry: ${cookies.length}`);
    }
    
    if (cookies.length === 0) {
      throw new Error('Terabox tidak memberikan cookie session. Kemungkinan IP terblokir atau server maintenance.');
    }
    
    // Ekstrak surl dari URL halaman (setelah redirect mungkin)
    const finalUrl = page.url();
    const surlFromPage = surlFromUrl(finalUrl);
    const surlFromInput = surlFromUrl(url);
    const surl = surlFromPage || surlFromInput;
    
    if (!surl) {
      throw new Error('Tidak bisa ekstrak kode surl dari link Terabox');
    }
    
    log(`Surl: ${surl}`);
    
    // Close browser - kita sudah punya cookies, tidak perlu browser lagi
    await browser.close();
    browser = null;
    
    // Cek apakah ada cookies yang tersimpan dari sesi sebelumnya
    const existingCookieFile = path.join(workDir, 'terabox_cookies.json');
    let existingCookies = [];
    if (fs.existsSync(existingCookieFile)) {
      try {
        existingCookies = JSON.parse(fs.readFileSync(existingCookieFile, 'utf8'));
        log(`Load cookies dari file: ${existingCookies.length} cookies`);
      } catch (e) {
        log('Gagal load cookies dari file, akan baru saja didapat');
      }
    }
    
    // Gabungkan cookies lama dengan yang baru (cookie baru lebih diprioritaskan)
    const existingCookieMap = new Map();
    for (const c of existingCookies) {
      existingCookieMap.set(c.name, c);
    }
    for (const c of cookies) {
      existingCookieMap.set(c.name, c);
    }
    cookies = Array.from(existingCookieMap.values());
    log(`Total cookies setelah merge: ${cookies.length}`);
    
    // Simpan cookies untuk digunakan nanti
    const cookieFile = path.join(workDir, 'terabox_cookies.json');
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    log(`Cookies disimpan ke: ${cookieFile}`);
    
    const referer = `https://www.terabox.com/sharing/link?surl=${surl}`;
    
    // Panggil API share/list untuk dapat daftar file
    log('Memanggil API share/list...');
    const apiParams = new URLSearchParams({
      app_id: '250528',
      channel: 'chunlei',
      web: '1',
      clienttype: '0',
      shorturl: surl,
      root: '1',
    });
    
    const apiUrl = `https://www.terabox.com/share/list?${apiParams}`;
    const apiResult = await fetchWithHeaders(apiUrl, cookies, referer);
    
    log(`API response status: ${apiResult.status}`);
    
    const apiData = parseApiResponse(apiResult.body);
    
    if (!apiData) {
      throw new Error('Respons API tidak valid (bukan JSON)');
    }
    
    if (apiData.errno !== 0 && apiData.errno !== undefined) {
      const errorMsg = interpretErrorCode(apiData.errno);
      throw new Error(`Terabox API error ${apiData.errno}: ${errorMsg}`);
    }
    
    const list = apiData.list || [];
    log(`Jumlah item: ${list.length}`);
    
    if (list.length === 0) {
      throw new Error('Share link kosong atau tidak bisa diakses');
    }
    
    // Single file handling
    if (list.length === 1 && String(list[0].isdir) !== '1') {
      const item = list[0];
      log(`Single file: ${item.server_filename || item.name}`);
      
      const dlink = await resolveSingleFileItem(item, cookies, surl, workDir);
      
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: dlink,
        filename: item.server_filename || item.name || 'terabox_file',
        cookie_file: cookieFile,
        referer: referer,
      }));
      return;
    }
    
    // Folder handling
    log('Folder detected, memproses isi...');
    const result = await processFolder(list, cookies, surl, workDir);
    
    if (result.files.length === 0) {
      throw new Error(
        `Folder berisi ${result.total} item, tetapi semua gagal di-resolve. ` +
        `Kemungkinan link terproteksi (butuh login/verify).`
      );
    }
    
    console.log(JSON.stringify({
      ok: true,
      type: 'folder',
      files: result.files,
      total: result.total,
      resolved: result.resolved,
      failed: result.failed,
      cookie_file: cookieFile,
      referer: referer,
    }));
    
  } catch (err) {
    log('ERROR:', err.message);
    console.log(JSON.stringify({ 
      ok: false, 
      error: String(err.message || err) 
    }));
    process.exitCode = 1;
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (e) {}
    }
  }
}

main();
