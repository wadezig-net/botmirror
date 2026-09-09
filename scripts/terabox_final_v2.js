/**
 * Terabox Download - Final Version with cookie persistence and verification handling
 * 
 * Features:
 * 1. Reuse cookies dari session sebelumnya
 * 2. Handle security verification popup jika muncul
 * 3. Multiple fallback strategies
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 30000,
  POST_NAV_DELAY: 5000,
  VERIFY_WAIT: 10000,
};

function log(...args) {
  console.error('[final]', new Date().toISOString(), ...args);
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

async function fetchWithHeaders(url, cookies, referer) {
  const headers = {
    'User-Agent': CONFIG.UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': referer || url,
    'Cookie': cookieHeader(cookies),
    'Connection': 'keep-alive',
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
  return new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    ...opts,
  });
}

/**
 * Cek hubungan antara cookies yang didapat dan kebutuhan verify
 */
function analyzeCookies(cookies) {
  const names = cookies.map(c => c.name);
  const hasAuth = names.some(n => n.includes('auth') || n.includes('token') || n.includes('session') || n.includes('bduss'));
  const hasTrack = names.some(n => n.includes('track') || n.includes('analytics'));
  const hasBasic = names.some(n => n === 'TSID' || n === 'csrfToken' || n === 'browserid');
  
  return {
    total: cookies.length,
    hasAuth,
    hasTrack,
    hasBasic,
    names: names.slice(0, 15).join(', '),
  };
}

/**
 * Setelah klik download, cek apakah ada popup verifikasi
 */
async function checkForVerificationModal(page) {
  const modalInfo = await page.evaluate(() => {
    const modals = document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="popup"], [class*="verify"]');
    const visibleModals = [];
    
    modals.forEach(modal => {
      const style = window.getComputedStyle(modal);
      if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
        visibleModals.push({
          tag: modal.tagName,
          className: modal.className,
          text: modal.textContent?.slice(0, 300),
          hasInput: modal.querySelector('input, textarea, button[type="submit"]') !== null,
        });
      }
    });
    
    return visibleModals;
  });
  
  return modalInfo;
}

/**
 * Coba close verifikasi modal dengan cara berbeda
 */
async function tryHandleVerification(page, maxAttempts = 3) {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    log(`  Coba handle verifikasi (attempt ${attempt + 1})...`);
    
    // Cek modal yang ada
    const modals = await checkForVerificationModal(page);
    log(`  Modal ditemukan: ${modals.length}`);
    
    if (modals.length === 0) {
      log('  Tidak ada modal verifikasi');
      return true;
    }
    
    // Analisis modal
    for (const modal of modals) {
      log(`  Modal: ${modal.className}, text: ${modal.text?.slice(0, 100)}`);
      
      // Jika ada tombol di dalam modal
      if (modal.hasInput) {
        // Coba klik tombol confirm/OK/continue
        const confirmSelectors = [
          'button:has-text("Continue")',
          'button:has-text("Confirm")',
          'button:has-text("Verify")',
          'button:has-text("Login")',
          'button:has-text("Sign up")',
          'button:has-text("Submit")',
          'button:has-text("OK")',
          'button:has-text("Yes")',
        ];
        
        for (const selector of confirmSelectors) {
          try {
            const btn = await page.$(selector);
            if (btn) {
              await btn.click();
              log(`  ✓ Klik tombol: ${selector}`);
              await page.waitForTimeout(2000);
              return true;
            }
          } catch {}
        }
        
        // Coba tekan Enter
        try {
          await page.keyboard.press('Enter');
          log('  ✓ Tekan Enter');
          await page.waitForTimeout(2000);
          return true;
        } catch {}
        
        // Coba tekan Escape
        try {
          await page.keyboard.press('Escape');
          log('  ✓ Tekan Escape');
          await page.waitForTimeout(2000);
        } catch {}
      }
    }
    
    await page.waitForTimeout(2000);
  }
  
  return false;
}

/**
 * Pendekatan utama: buka halaman, klik download, tangani verifikasi
 */
async function tryBrowserDownload(url, surl, cookies, workDir) {
  const urlObj = new URL(url);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;
  const referer = `${baseUrl}/sharing/link?surl=${surl}`;
  
  log('Pendekatan browser: membuka halaman dan mencoba download...');
  
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  
  try {
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
      acceptDownloads: true,
      cookies: cookies, // Inject cookies yang sudah ada
    });
    
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
    
    const page = await context.newPage();
    
    // Track semua response
    let capturedDlink = null;
    page.on('response', async (response) => {
      try {
        const body = await response.text();
        if (body.includes('dlink') || body.includes('download_url')) {
          const data = JSON.parse(body);
          if (data.dlink && data.dlink.startsWith('http')) {
            capturedDlink = data.dlink;
            log(`  ✓ Ditemukan dlink di response: ${capturedDlink.slice(0, 100)}`);
          }
        }
      } catch {}
    });
    
    // Navigasi
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: CONFIG.BROWSER_TIMEOUT }).catch(() => {});
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Cek halaman
    log('  Current URL:', page.url());
    
    // Cari tombol download
    log('  Mencari tombol download...');
    const downloadBtns = await page.$$('a[class*="download"], button[class*="download"], a:has-text("Download"), button:has-text("Download")');
    log(`  Tombol download ditemukan: ${downloadBtns.length}`);
    
    if (downloadBtns.length === 0) {
      await browser.close();
      return null;
    }
    
    // Klik tombol download
    await downloadBtns[0].click();
    log('  ✓ Klik tombol download');
    
    // Tunggu sebentar
    await page.waitForTimeout(2000);
    
    // Handle verifikasi jika muncul
    const verifyHandled = await tryHandleVerification(page);
    
    if (verifyHandled) {
      // Tunggu untuk proses download
      await page.waitForTimeout(CONFIG.VERIFY_WAIT);
      
      // Cek hasil
      if (capturedDlink) {
        await browser.close();
        return capturedDlink;
      }
      
      // Ekstrak dari halaman
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
        await browser.close();
        return pageData[0];
      }
    }
    
    await browser.close();
    return null;
    
  } catch (e) {
    log(`  Error: ${e.message}`);
    try { await browser.close(); } catch {}
    return null;
  }
}

/**
 * Fallback: coba API calls dengan cookies yang ada
 */
async function tryApiDownload(surl, cookies, workDir) {
  const urlObj = new URL(process.argv[2]);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;
  const referer = `${baseUrl}/sharing/link?surl=${surl}`;
  
  log('Pendekatan API: mencoba call langsung...');
  
  // Cek apakah ada file yang bisa di-download langsung
  const listParams = buildListParams(surl, { root: '1' });
  const listResult = await fetchWithHeaders(
    `https://www.terabox.com/share/list?${listParams}`,
    cookies,
    referer
  );
  
  const listData = parseApiResponse(listResult.body);
  if (!listData || listData.errno !== 0) {
    log(`  List API gagal: errno=${listData?.errno}`);
    return null;
  }
  
  // Jika ada file langsung (bukan folder)
  const files = listData.list?.filter(item => String(item.isdir) !== '1');
  if (files && files.length > 0) {
    const file = files[0];
    log(`  File langsung ditemukan: ${file.server_filename}`);
    
    // Coba ambil dlink
    const fsid = file.fs_id || file.fsid;
    const dlParams = buildListParams(surl, { fsid: String(fsid), rt: '1', type: 'dlink' });
    const dlResult = await fetchWithHeaders(
      `https://www.terabox.com/share/download?${dlParams}`,
      cookies,
      referer
    );
    
    const dlData = parseApiResponse(dlResult.body);
    if (dlData && dlData.errno === 0 && dlData.dlink) {
      log(`  ✓ Dlink didapat via API: ${dlData.dlink.slice(0, 100)}`);
      return {
        url: dlData.dlink,
        filename: file.server_filename,
        method: 'api_direct',
      };
    }
    
    log(`  API download gagal: errno=${dlData?.errno}`);
  }
  
  // Jika folder, traverse rekursif
  const folders = listData.list?.filter(item => String(item.isdir) === '1');
  if (folders && folders.length > 0) {
    log(`  Folder ditemukan, traverse...`);
    
    const allFiles = [];
    const folderPath = folders[0].path;
    
    const traverseParams = buildListParams(surl, { root: '0', dir: folderPath });
    const traverseResult = await fetchWithHeaders(
      `https://www.terabox.com/share/list?${traverseParams}`,
      cookies,
      referer
    );
    
    const traverseData = parseApiResponse(traverseResult.body);
    if (traverseData && traverseData.errno === 0 && traverseData.list) {
      for (const item of traverseData.list) {
        if (String(item.isdir) !== '1') {
          allFiles.push(item);
        }
      }
    }
    
    log(`  Total file setelah traverse: ${allFiles.length}`);
    
    if (allFiles.length > 0) {
      // Coba ambil dlink untuk file pertama
      const file = allFiles[0];
      const fsid = file.fs_id || file.fsid;
      const dlParams = buildListParams(surl, { fsid: String(fsid), rt: '1', type: 'dlink' });
      const dlResult = await fetchWithHeaders(
        `https://www.terabox.com/share/download?${dlParams}`,
        cookies,
        referer
      );
      
      const dlData = parseApiResponse(dlResult.body);
      if (dlData && dlData.errno === 0 && dlData.dlink) {
        log(`  ✓ Dlink didapat via API traverse: ${dlData.dlink.slice(0, 100)}`);
        return {
          url: dlData.dlink,
          filename: file.server_filename,
          method: 'api_traverse',
        };
      }
    }
  }
  
  return null;
}

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
  
  log('Terabox Download Final Version');
  log('URL:', url);
  
  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ ok: false, error: 'Tidak bisa ekstrak surl' }));
    process.exit(1);
  }
  
  log('Surl:', surl);
  
  let browser;
  
  try {
    // Buka browser untuk dapat cookies
    log('Membuka browser untuk dapat session...');
    browser = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    });
    
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
    });
    
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
    
    const page = await context.newPage();
    page.on('dialog', async (d) => d.dismiss().catch(() => {}));
    
    // Navigasi
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: CONFIG.BROWSER_TIMEOUT }).catch(() => {});
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Dapatkan cookies
    let cookies = await context.cookies();
    log(`Dapat ${cookies.length} cookies`);
    
    if (cookies.length === 0) {
      await browser.close();
      console.log(JSON.stringify({ 
        ok: false, 
        error: 'Tidak dapat session cookies. IP mungkin terblokir.' 
      }));
      process.exit(1);
    }
    
    // Analyze cookies
    const cookieAnalysis = analyzeCookies(cookies);
    log('Cookie analysis:', cookieAnalysis);
    
    // Simpan cookies
    const cookieFile = path.join(workDir, 'terabox_cookies.json');
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    
    // Tutup browser awal
    await browser.close();
    browser = null;
    
    // Coba API approach dulu (lebih cepat)
    log('\\nMencoba API approach...');
    const apiResult = await tryApiDownload(surl, cookies, workDir);
    
    if (apiResult) {
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: apiResult.url,
        filename: apiResult.filename,
        cookie_file: cookieFile,
        referer: `${baseUrl}/sharing/link?surl=${surl}`,
        method: apiResult.method,
      }));
      return;
    }
    
    // Jika API gagal, coba browser approach
    log('\\nAPI gagal, mencoba browser approach...');
    
    // Buka browser lagi untuk browser interaction
    browser = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    });
    
    const browserResult = await tryBrowserDownload(url, surl, cookies, workDir);
    
    if (browserResult) {
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: browserResult,
        filename: 'file_from_browser',
        cookie_file: cookieFile,
        referer: `${baseUrl}/sharing/link?surl=${surl}`,
        method: 'browser_interaction',
      }));
    } else {
      console.log(JSON.stringify({
        ok: false,
        error: 'Gagal mendapatkan download URL. Terabox memerlukan verifikasi tambahan (login/captcha).',
        suggestions: [
          'Gunakan cookie dari akun Terabox yang sudah login',
          'Buka link di browser manual, lakukan login, lalu coba lagi',
          'Cek apakah link ini memang publik atau butuh akses khusus',
        ],
        debug: {
          cookies: cookieAnalysis,
          url: url,
        }
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
