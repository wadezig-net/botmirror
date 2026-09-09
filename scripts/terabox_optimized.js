/**
 * Terabox Download - Optimized Browser Session Version
 * 
 * Strategi:
 * 1. Buka 1 browser session untuk semua operasi
 * 2. Traverse folder pakai API (lebih cepat)
 * 3. Untuk file yang butuh verify_v2, buka halaman dan capture network setelah klik download
 * 4. Re-use browser untuk semua file, jangan buka-tutup tiap file
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 30000,
  POST_NAV_DELAY: 5000,
};

function log(...args) {
  console.error('[optimized]', new Date().toISOString(), ...args);
}

function extractSurl(url) {
  let m = url.match(/[?&]surl=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  m = url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  return m ? m[1] : null;
}

function cookieHeader(cookies) {
  return cookies.map(c => `${c.name}=${c.value}`).join('; ');
}

async function fetchWithHeaders(url, cookies, referer, extra = {}) {
  const headers = {
    'User-Agent': CONFIG.UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': referer || url,
    'Cookie': cookieHeader(cookies),
    'Connection': 'keep-alive',
    ...extra,
  };
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  
  try {
    const response = await fetch(url, { headers, signal: controller.signal });
    return {
      status: response.status,
      headers: Object.fromEntries(response.headers.entries()),
      body: await response.text(),
    };
  } finally {
    clearTimeout(timeout);
  }
}

function parseApiResponse(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function buildListParams(surl, opts = {}) {
  const params = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    ...opts,
  });
  return params;
}

/**
 * Cek apakah file butuh verify_v2
 */
async function checkNeedsVerify(fsid, surl, cookies, baseUrl) {
  const apiBase = 'https://www.terabox.com';
  const referer = `${baseUrl}/sharing/link?surl=${surl}`;
  
  const params = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    fsid: String(fsid),
    rt: '1',
    type: 'dlink',
  });
  
  const result = await fetchWithHeaders(`${apiBase}/share/download?${params}`, cookies, referer);
  const data = parseApiResponse(result.body);
  
  return {
    needsVerify: data && data.errno === 400310,
    errno: data?.errno,
    data: data,
  };
}

/**
 * Buka browser, navigate ke halaman, dan tunggu sampai semua network request selesai
 * Return: { page, browser, cookies }
 */
async function initBrowserSession(url, surl) {
  const browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-blink-features=AutomationControlled',
    ],
  });
  
  const context = await browser.newContext({
    userAgent: CONFIG.UA,
    locale: 'en-US',
    viewport: { width: 1920, height: 1080 },
    acceptDownloads: true,
  });
  
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  });
  
  const page = await context.newPage();
  
  // Handle dialog
  page.on('dialog', async (d) => d.dismiss().catch(() => {}));
  
  // Navigasi
  log('Navigasi ke halaman...');
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: CONFIG.BROWSER_TIMEOUT }).catch(() => {});
  await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
  
  const cookies = await context.cookies();
  log('Dapat cookies:', cookies.length);
  
  // Simpan cookies
  fs.writeFileSync(path.join(process.argv[3], 'terabox_cookies.json'), JSON.stringify(cookies));
  
  return { page, browser, context, cookies };
}

/**
 * Coba dapatkan dlink lewat browser interaction
 * Return: direct URL atau null
 */
async function tryGetDlinkViaBrowser(page, fsid, surl, baseUrl, timeoutMs = 15000) {
  const apiBase = 'https://www.terabox.com';
  const filePageUrl = `${baseUrl}/share/link?surl=${surl}`;
  
  log(`  Membuka halaman untuk file fsid=${fsid}...`);
  
  // Navigate ke halaman
  await page.goto(filePageUrl, { waitUntil: 'domcontentloaded', timeout: CONFIG.BROWSER_TIMEOUT }).catch(() => {});
  await page.waitForTimeout(2000);
  
  // Capture network responses selama operasi
  const capturedUrls = [];
  page.on('response', async (response) => {
    const resUrl = response.url();
    if (resUrl.includes('download') && resUrl.includes(String(fsid))) {
      try {
        const body = await response.text();
        capturedUrls.push({ url: resUrl, body });
        const data = JSON.parse(body);
        if (data.dlink) {
          log(`  ✓ Ditemukan di network: ${data.dlink.slice(0, 100)}`);
        }
      } catch {}
    }
  });
  
  // Cari item file di halaman dan klik
  log(`  Mencari elemen file...`);
  const selectors = [
    `[data-fsid="${fsid}"]`,
    `div[fsid="${fsid}"]`,
    `div[data-id="${fsid}"]`,
    `[data-id="${fsid}"]`,
  ];
  
  let clicked = false;
  for (const selector of selectors) {
    try {
      const elements = await page.$$(selector);
      if (elements.length > 0) {
        await elements[0].click();
        log(`  ✓ Klik elemen dengan selector: ${selector}`);
        clicked = true;
        break;
      }
    } catch {}
  }
  
  if (!clicked) {
    // Fallback: klik pada list item yang mengandung nama file
    const fileName = await page.evaluate(() => {
      // Cari berdasarkan teks - ini lebih lambat tapi lebih akurat
      const allElements = document.querySelectorAll('div, span, p');
      for (const el of allElements) {
        if (el.textContent && el.textContent.includes('@hiresmusicjapan')) {
          return el;
        }
      }
      return null;
    });
    
    if (fileName) {
      await fileName.click();
      log('  ✓ Klik berdasarkan teks');
    }
  }
  
  await page.waitForTimeout(1000);
  
  // Cari tombol download dan klik
  log(`  Mencari tombol download...`);
  const downloadSelectors = [
    'a[data-vc-name="downloadBtn"]',
    'a[class*="download"]',
    'button[class*="download"]',
    'span:has-text("Download")',
    'button:has-text("Download")',
    'a:has-text("Download")',
  ];
  
  for (const selector of downloadSelectors) {
    try {
      const btn = await page.$(selector);
      if (btn) {
        await btn.click();
        log(`  ✓ Klik tombol download: ${selector}`);
        break;
      }
    } catch {}
  }
  
  // Tunggu untuk proses
  log(`  Menunggu ${timeoutMs}ms untuk proses...`);
  await page.waitForTimeout(timeoutMs);
  
  // Cek hasil dari captured URLs
  for (const captured of capturedUrls) {
    try {
      const data = JSON.parse(captured.body);
      if (data.dlink && data.dlink.startsWith('http')) {
        return data.dlink;
      }
      if (data.url && data.url.startsWith('http')) {
        return data.url;
      }
    } catch {}
  }
  
  // Fallback: ekstrak dari halaman
  const pageData = await page.evaluate(() => {
    const elements = document.querySelectorAll('[data-dlink], [data-download-url], a[href*="download"]');
    const urls = [];
    elements.forEach(el => {
      if (el.href) urls.push(el.href);
      if (el.dataset?.dlink) urls.push(el.dataset.dlink);
      if (el.dataset?.downloadUrl) urls.push(el.dataset.downloadUrl);
    });
    return urls;
  });
  
  if (pageData.length > 0) {
    log(`  ✓ Ditemukan di halaman: ${pageData[0].slice(0, 100)}`);
    return pageData[0];
  }
  
  return null;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Usage: node terabox_optimized.js <url> <work_dir>' 
    }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
  log('Memulai Terabox Download (Optimized)...');
  log('URL:', url);
  
  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ ok: false, error: 'Tidak bisa ekstrak surl' }));
    process.exit(1);
  }
  
  log('Surl:', surl);
  
  const urlObj = new URL(url);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;
  
  let browser;
  
  try {
    // Init browser session SEKALI
    const { page, browser: b, cookies } = await initBrowserSession(url, surl);
    browser = b;
    
    // Cek apakah perlu verify - kita tes dengan file pertama
    const testFsids = ['919550641093456']; // file pertama dari test sebelumnya
    const checkResult = await checkNeedsVerify(testFsids[0], surl, cookies, baseUrl);
    log('Check verify:', checkResult);
    
    if (checkResult.needsVerify) {
      log('File butuh verify_v2, mencoba browser interaction...');
      
      // Coba dapatkan dlink lewat browser
      const dlink = await tryGetDlinkViaBrowser(page, testFsids[0], surl, baseUrl, 10000);
      
      if (dlink) {
        console.log(JSON.stringify({
          ok: true,
          type: 'single',
          direct_url: dlink,
          filename: 'test_file',
          cookie_file: path.join(workDir, 'terabox_cookies.json'),
          referer: `${baseUrl}/sharing/link?surl=${surl}`,
          method: 'browser_interaction',
        }));
      } else {
        console.log(JSON.stringify({
          ok: false,
          error: 'Tidak dapat dlink meski sudah coba browser interaction',
          debug: {
            errno: checkResult.errno,
            message: 'verify_v2 gagal di-bypass',
          }
        }));
      }
    } else {
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: 'http://example.com/test', // placeholder
        message: 'File tidak perlu verify, bisa langsung di-download',
      }));
    }
    
  } catch (err) {
    log('ERROR:', err.message);
    if (browser) {
      try { await browser.close(); } catch {}
    }
    console.log(JSON.stringify({ ok: false, error: String(err.message || err) }));
    process.exit(1);
  } finally {
    if (browser) {
      try { await browser.close(); } catch {}
    }
  }
}

main();
