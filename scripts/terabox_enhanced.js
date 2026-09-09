/**
 * Terabox Download - Enhanced Version
 * Supports multiple link formats including wap endpoints
 * Better folder handling with multiple resolution strategies
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 30000,
  POST_NAV_DELAY: 3000,
  COOKIE_RETRY_DELAY: 5000,
  API_TIMEOUT: 30000,
};

function log(...args) {
  console.error('[terabox]', new Date().toISOString(), ...args);
}

/**
 * Ekstrak surl dari berbagai format URL Terabox
 * Mendukung: standar, wap, share/link, share/filelist, dll
 */
function extractSurl(url) {
  // Format wap: /wap/share/filelist?surl=XXX
  let m = url.match(/[?&]surl=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  
  // Format /s/XXX
  m = url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  
  // Format /sharing/link/XXX
  m = url.match(/\/sharing\/link\/([^/?&]+)/);
  if (m) return m[1];
  
  // Path segment terakhir (6-32 chars)
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  if (m) return m[1];
  
  return null;
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
  const timeout = setTimeout(() => controller.abort(), CONFIG.API_TIMEOUT);
  
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

/**
 * Build API params untuk share/list berdasarkan konteks
 */
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
 * Coba resolve folder dengan multiple pendekatan
 */
async function resolveFolderMultiApproach(surl, cookies, baseUrl, workDir) {
  const apiBase = 'https://www.terabox.com';
  const referer = `${baseUrl}/sharing/link?surl=${surl}`;
  
  // Pendekatan 1: API standar dengan root=1 (semua konten)
  log('Pendekatan 1: API share/list dengan root=1');
  const listParams1 = buildListParams(surl, { root: '1' });
  let result1 = await fetchWithHeaders(`${apiBase}/share/list?${listParams1}`, cookies, referer);
  let data1 = parseApiResponse(result1.body);
  
  if (data1 && data1.errno === 0 && data1.list && data1.list.length > 0) {
    log(`Dapat ${data1.list.length} item dari pendekatan 1`);
    return data1.list;
  }
  
  // Pendekatan 2: Tanpa root parameter
  log('Pendekatan 2: API share/list tanpa root param');
  const listParams2 = buildListParams(surl);
  let result2 = await fetchWithHeaders(`${apiBase}/share/list?${listParams2}`, cookies, referer);
  let data2 = parseApiResponse(result2.body);
  
  if (data2 && data2.errno === 0 && data2.list && data2.list.length > 0) {
    log(`Dapat ${data2.list.length} item dari pendekatan 2`);
    return data2.list;
  }
  
  // Pendekatan 3: Render halaman dan ekstrak dari HTML/JS
  log('Pendekatan 3: Ekstrak dari halaman yang dirender');
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  
  try {
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
    });
    
    const page = await context.newPage();
    
    // Inject script untuk capture network requests
    const capturedUrls = [];
    page.on('response', (response) => {
      const url = response.url();
      if (url.includes('share/list') || url.includes('share/download') || url.includes('dlink')) {
        capturedUrls.push({ url, status: response.status(), body: response.text() });
      }
    });
    
    await page.goto(baseUrl + '/share/link?surl=' + surl, { 
      waitUntil: 'networkidle', 
      timeout: 30000 
    }).catch(() => {});
    
    await page.waitForTimeout(5000);
    
    // Ekstrak dari captured network responses
    for (const captured of capturedUrls) {
      try {
        const data = JSON.parse(captured.body);
        if (data && data.list && data.list.length > 0) {
          log(`Dapat ${data.list.length} item dari network capture`);
          await browser.close();
          return data.list;
        }
      } catch {}
    }
    
    // Fallback: ekstrak dari HTML content
    const content = await page.content();
    
    // Cari 패턴을 di HTML
    const patterns = [
      /"list"\s*:\s*(\[.*?\])/s,
      /window\.pageData\s*=\s*({.*?});/s,
      /var\s+data\s*=\s*({.*?});/s,
    ];
    
    for (const pattern of patterns) {
      const match = content.match(pattern);
      if (match) {
        try {
          const parsed = JSON.parse(match[1]);
          if (parsed.list && parsed.list.length > 0) {
            log(`Dapat ${parsed.list.length} item dari HTML parse`);
            await browser.close();
            return parsed.list;
          }
        } catch {}
      }
    }
    
    await browser.close();
    
  } catch (e) {
    log('Error di pendekatan 3:', e.message);
    if (browser) await browser.close();
  }
  
  return null;
}

/**
 * Resolve dlink untuk item individual
 */
async function resolveItemDlink(item, surl, cookies, baseUrl, workDir) {
  const apiBase = 'https://www.terabox.com';
  const referer = `${baseUrl}/sharing/link?surl=${surl}`;
  const fsid = item.fs_id || item.fsid;
  const name = item.server_filename || item.name || 'unknown';
  
  if (item.dlink && item.dlink.startsWith('http')) {
    log(`  ✓ Dlink langsung: ${name}`);
    return item.dlink;
  }
  
  // Cek apakah perlu verify_v2
  const checkParams = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    fsid: String(fsid),
    rt: '1',
    type: 'dlink',
  });
  const checkUrl = `${apiBase}/share/download?${checkParams}`;
  const checkResult = await fetchWithHeaders(checkUrl, cookies, referer);
  const checkData = parseApiResponse(checkResult.body);
  
  // Jika perlu verify_v2, kita perlu buka halaman di browser
  if (checkData && checkData.errno === 400310) {
    log(`  ! File ${name} perlu verify_v2, coba pendekatan browser...`);
    
    // Restart browser untuk pendekatan interaktif
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
      });
      
      await context.addInitScript(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      });
      
      const page = await context.newPage();
      
      // Capture network responses
      let foundDlink = null;
      page.on('response', async (response) => {
        const resUrl = response.url();
        if (resUrl.includes('download') && resUrl.includes(String(fsid))) {
          try {
            const body = await response.text();
            const data = JSON.parse(body);
            if (data.dlink && data.dlink.startsWith('http')) {
              foundDlink = data.dlink;
              log(`  ✓ Ditemukan di network: ${foundDlink.slice(0, 100)}`);
            }
          } catch {}
        }
      });
      
      // Navigasi ke halaman file
      const filePageUrl = `${baseUrl}/share/link?surl=${surl}`;
      await page.goto(filePageUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(3000);
      
      // Coba cari dan klik tombol download untuk file ini
      // Kita butuh cara untuk select file tertentu - biasanya klik pada item list
      const fileItems = await page.$$('div[class*="fileItem"], div[class*="file-item"], [data-fsid="' + fsid + '"]');
      
      if (fileItems.length > 0) {
        // Klik pada item file untuk select
        await fileItems[0].click();
        await page.waitForTimeout(1000);
      }
      
      // Cari dan klik tombol download
      const downloadSelectors = [
        'a[data-vc-name="downloadBtn"]',
        'a[class*="downloadBtn"]',
        'a[class*="download"]',
        'span:has-text("Download")',
        'button:has-text("Download")',
      ];
      
      for (const selector of downloadSelectors) {
        try {
          const btn = await page.$(selector);
          if (btn) {
            await btn.click();
            log(`  ✓ Klik tombol download (${selector})`);
            break;
          }
        } catch {}
      }
      
      // Tunggu untuk proses download/verifikasi
      await page.waitForTimeout(5000);
      
      // Jika ditemukan di network, return
      if (foundDlink) {
        await browser.close();
        return foundDlink;
      }
      
      // Fallback: ekstrak dari halaman
      const pageContent = await page.evaluate(() => {
        const elements = document.querySelectorAll('[data-dlink], [data-download-url], a[href*="download"]');
        const urls = [];
        elements.forEach(el => {
          if (el.href) urls.push(el.href);
          if (el.dataset.dlink) urls.push(el.dataset.dlink);
          if (el.dataset.downloadUrl) urls.push(el.dataset.downloadUrl);
        });
        return urls;
      });
      
      if (pageContent.length > 0) {
        log(`  ✓ Ditemukan di halaman: ${pageContent[0].slice(0, 100)}`);
        await browser.close();
        return pageContent[0];
      }
      
      await browser.close();
      
    } catch (e) {
      log(`  Error di browser approach: ${e.message}`);
      try { await browser.close(); } catch {}
    }
  }
  
  // Jika tidak perlu verify atau setelah gagal, coba endpoint biasa
  const endpoints = [
    { path: '/share/download', params: { rt: '1', type: 'dlink' } },
    { path: '/share/download', params: { type: 'download', wlclient: '1' } },
    { path: '/share/download', params: { rt: '1', type: 'dlink', force: '1' } },
    { path: '/share/download', params: { dfs: '1', force: '1' } },
  ];
  
  for (const ep of endpoints) {
    const params = new URLSearchParams({
      app_id: '250528',
      channel: 'chunlei',
      web: '1',
      clienttype: '0',
      shorturl: surl,
      fsid: String(fsid),
      ...ep.params,
    });
    params.delete('root');
    
    const url = `${apiBase}${ep.path}?${params}`;
    const result = await fetchWithHeaders(url, cookies, referer);
    const data = parseApiResponse(result.body);
    
    if (data && data.errno === 0) {
      if (data.dlink && data.dlink.startsWith('http')) {
        log(`  ✓ Dlink dari ${ep.path}: ${name}`);
        return data.dlink;
      }
      if (data.url && data.url.startsWith('http')) {
        log(`  ✓ URL dari ${ep.path}: ${name}`);
        return data.url;
      }
      if (data.data && data.data.dlink) {
        return data.data.dlink;
      }
    }
  }
  
  // Fallback terakhir
  const fallbackParams = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    fsid: String(fsid),
    type: 'file',
    t: '1',
  });
  const fallbackUrl = `${apiBase}/share/download?${fallbackParams}`;
  const fallbackResult = await fetchWithHeaders(fallbackUrl, cookies, referer);
  
  try {
    const fbData = JSON.parse(fallbackResult.body);
    if (fbData && fbData.errno === 0) {
      if (fbData.dlink) return fbData.dlink;
      if (fbData.data && fbData.data.dlink) return fbData.data.dlink;
      if (fbData.url) return fbData.url;
    }
  } catch {}
  
  log(`  ✗ Gagal resolve dlink untuk: ${name} (errno: ${checkData?.errno || 'unknown'})`);
  return null;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Usage: node terabox_enhanced.js <url> <work_dir>' 
    }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
  log('Memulai proses download Terabox...');
  log('URL:', url);
  
  // Ekstrak surl
  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Tidak bisa ekstrak surl dari URL yang diberikan' 
    }));
    process.exit(1);
  }
  
  log('Surl yang diekstrak:', surl);
  
  // Tentukan base URL (domain tanpa path)
  const urlObj = new URL(url);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;
  
  let browser;
  
  try {
    // Buka browser untuk dapat cookies
    log('Membuka browser untuk dapat session cookies...');
    browser = await chromium.launch({
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
    });
    
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
      });
    });
    
    const page = await context.newPage();
    page.on('dialog', async (d) => d.dismiss().catch(() => {}));
    
    // Navigasi ke URL asli (bisa wap atau standar)
    log('Navigasi ke halaman share...');
    await page.goto(url, { 
      waitUntil: 'domcontentloaded', 
      timeout: CONFIG.BROWSER_TIMEOUT 
    }).catch(e => log('Navigate warning:', e.message));
    
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Dapatkan cookies
    let cookies = await context.cookies();
    log(`Cookie yang didapat: ${cookies.length}`);
    
    if (cookies.length === 0) {
      log('Tidak ada cookie, retry...');
      await page.waitForTimeout(CONFIG.COOKIE_RETRY_DELAY);
      cookies = await context.cookies();
      log(`Cookie setelah retry: ${cookies.length}`);
    }
    
    if (cookies.length === 0) {
      throw new Error('Tidak dapat session cookies dari Terabox. IP mungkin terblokir.');
    }
    
    // Simpan cookies
    const cookieFile = path.join(workDir, 'terabox_cookies.json');
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    log('Cookies disimpan ke:', cookieFile);
    
    // Tutup browser setelah dapat cookies
    await browser.close();
    browser = null;
    
    // Coba resolve folder dengan multiple pendekatan
    log('Mencoba resolve folder/list...');
    const list = await resolveFolderMultiApproach(surl, cookies, baseUrl, workDir);
    
    if (!list || list.length === 0) {
      // Try direct API call as last resort
      log('Mencoba panggil API langsung...');
      const apiParams = buildListParams(surl, { root: '1' });
      const apiResult = await fetchWithHeaders(
        `https://www.terabox.com/share/list?${apiParams}`, 
        cookies, 
        `${baseUrl}/sharing/link?surl=${surl}`
      );
      const apiData = parseApiResponse(apiResult.body);
      
      if (apiData && apiData.errno === 0 && apiData.list) {
        log(`API langsung dapat ${apiData.list.length} item`);
        // Lanjutkan dengan list ini
        list.length = 0;
        list.push(...apiData.list);
      } else {
        console.log(JSON.stringify({ 
          ok: false, 
          error: `Tidak dapat mengambil daftar file. Error: ${apiData?.errno || 'unknown'} - ${apiData?.errmsg || 'Gagal mengambil data'}` 
        }));
        process.exit(1);
      }
    }
    
    // Cek apakah list berisi folder murni (perlu traverse rekursif)
    const hasOnlyFolders = list.length > 0 && list.every(item => String(item.isdir) === '1');
    
    if (hasOnlyFolders) {
      log('List berisi folder murni, melakukan traverse rekursif...');
      
      const allFiles = [];
      
      async function traverseFolder(folderPath, depth = 0) {
        if (depth > 10) {
          log(`Melebihi depth maks (${depth}), berhenti recurse pada: ${folderPath}`);
          return;
        }
        
        const listParams = buildListParams(surl, { root: '0', dir: folderPath || '/' });
        const apiResult = await fetchWithHeaders(
          `https://www.terabox.com/share/list?${listParams}`,
          cookies,
          `${baseUrl}/sharing/link?surl=${surl}`
        );
        
        const data = parseApiResponse(apiResult.body);
        if (!data || data.errno !== 0 || !data.list) {
          log(`Gagal list ${folderPath}: errno=${data?.errno || '?'}`);
          return;
        }
        
        log(`  List folder ${folderPath}: ${data.list.length} item (depth ${depth})`);
        
        for (const item of data.list) {
          if (String(item.isdir) === '1') {
            await traverseFolder(item.path, depth + 1);
          } else {
            allFiles.push({ ...item, fullPath: item.path, parentPath: folderPath });
            log(`    File: ${item.server_filename} (${item.size || 0}b)`);
          }
        }
      }
      
      for (const folderItem of list) {
        await traverseFolder(folderItem.path, 0);
      }
      
      log(`Total file setelah traverse: ${allFiles.length}`);
      
      if (allFiles.length === 0) {
        console.log(JSON.stringify({ ok: false, error: 'Tidak ada file ditemukan setelah traverse folder' }));
        process.exit(1);
      }
      
      // Resolve dlink untuk setiap file hasil traverse
      log('Memulai resolve dlink...');
      const resolvedFiles = [];
      let failedCount = 0;
      
      for (let i = 0; i < allFiles.length; i++) {
        const item = allFiles[i];
        log(`[${i + 1}/${allFiles.length}] ${item.server_filename || item.name}`);
        const dlink = await resolveItemDlink(item, surl, cookies, baseUrl, workDir);
        if (dlink) {
          resolvedFiles.push({
            url: dlink,
            filename: item.server_filename || item.name || 'unknown',
            size: item.size,
            fsid: item.fs_id || item.fsid,
            path: item.path,
          });
        } else {
          failedCount++;
        }
      }
      
      // Gunakan resolvedFiles untuk output
      console.log(JSON.stringify({
        ok: true,
        type: 'folder',
        files: resolvedFiles,
        total: allFiles.length,
        resolved: resolvedFiles.length,
        failed: failedCount,
        cookie_file: cookieFile,
        referer: `${baseUrl}/sharing/link?surl=${surl}`,
      }));
      return;
    }
    
    // List sudah berisi file langsung (bukan folder)
    const files = list.filter(item => String(item.isdir) !== '1');
    log(`Total file yang akan di-download: ${files.length}`);
    
    if (files.length === 0) {
      console.log(JSON.stringify({ 
        ok: false, 
        error: 'Tidak ada file yang ditemukan dalam link ini (hanya folder tanpa akses)' 
      }));
      process.exit(1);
    }
    
    // Resolve dlink untuk setiap file
    log('Memulai resolve dlink untuk setiap file...');
    const resolvedFiles = [];
    let failedCount = 0;
    
    for (let i = 0; i < files.length; i++) {
      const item = files[i];
      log(`[${i + 1}/${files.length}] Processing: ${item.server_filename || item.name}`);
      
      const dlink = await resolveItemDlink(item, surl, cookies, baseUrl, workDir);
      
      if (dlink) {
        resolvedFiles.push({
          url: dlink,
          filename: item.server_filename || item.name || 'unknown',
          size: item.size,
          fsid: item.fs_id || item.fsid,
        });
      } else {
        failedCount++;
      }
    }
    
    log(`Selesai: ${resolvedFiles.length} berhasil, ${failedCount} gagal`);
    
    if (resolvedFiles.length === 0) {
      console.log(JSON.stringify({ 
        ok: false, 
        error: 'Semua file gagal di-resolve. Link mungkin terproteksi (butuh login/verify).' 
      }));
      process.exit(1);
    }
    
    // Return hasil
    console.log(JSON.stringify({
      ok: true,
      type: 'folder',
      files: resolvedFiles,
      total: files.length,
      resolved: resolvedFiles.length,
      failed: failedCount,
      cookie_file: cookieFile,
      referer: `${baseUrl}/sharing/link?surl=${surl}`,
    }));
    
  } catch (err) {
    log('ERROR:', err.message);
    
    // Pastikan browser tertutup di error
    if (browser) {
      try { await browser.close(); } catch {}
    }
    
    console.log(JSON.stringify({ 
      ok: false, 
      error: String(err.message || err) 
    }));
    process.exit(1);
  }
}

main();
