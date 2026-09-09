/**
 * Terabox Download - Browser Interaction Version
 * 
 * Strategy:
 * 1. Buka browser, dapat cookies
 * 2. Navigasi ke halaman share
 * 3. Klik tombol download / tunggu proses di halaman
 * 4. Capture network traffic untuk dapat direct download URL
 * 5. Return URL yang didapat
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 60000,
  POST_NAV_DELAY: 5000,
  DOWNLOAD_WAIT: 15000,
};

function log(...args) {
  console.error('[browser]', new Date().toISOString(), ...args);
}

function extractSurl(url) {
  let m = url.match(/[?&]surl=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  m = url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  return m ? m[1] : null;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Usage: node terabox_browser.js <url> <work_dir>' 
    }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
  log('Memulai browser interaction mode...');
  log('URL:', url);
  
  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Tidak bisa ekstrak surl dari URL' 
    }));
    process.exit(1);
  }
  
  log('Surl:', surl);
  
  let browser;
  
  try {
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
      acceptDownloads: true,
    });
    
    // Anti-detection
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
      });
    });
    
    const page = await context.newPage();
    
    // Capture semua network response
    const networkUrls = [];
    page.on('response', async (response) => {
      const url = response.url();
      const status = response.status();
      
      // Filter URL yang relevan
      if (url.includes('download') || 
          url.includes('dlink') || 
          url.includes('file') ||
          url.includes('share') ||
          url.includes('vcode') ||
          url.includes('verify')) {
        try {
          const body = await response.text();
          networkUrls.push({
            url,
            status,
            body: body.slice(0, 5000), // Cap untuk menghindari memory issue
          });
          log('Network captured:', url.slice(0, 100), status);
        } catch {}
      }
    });
    
    // Handle dialog
    page.on('dialog', async (dialog) => {
      log('Dialog:', dialog.message());
      await dialog.dismiss().catch(() => {});
    });
    
    // Navigasi ke halaman
    log('Navigasi ke halaman share...');
    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: CONFIG.BROWSER_TIMEOUT,
    }).catch(e => log('Navigate error:', e.message));
    
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Dapatkan cookies
    const cookies = await context.cookies();
    log('Cookie count:', cookies.length);
    
    // Simpan cookies
    const cookieFile = path.join(workDir, 'terabox_cookies.json');
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    
    // Cek halaman saat ini
    const currentUrl = page.url();
    log('Current URL after nav:', currentUrl);
    
    // Tunggu konten 로드
    await page.waitForTimeout(3000);
    
    // Coba cari tombol download / link download di halaman
    log('Mencari elemen download di halaman...');
    
    // Coba ekstrak informasi dari halaman
    const pageData = await page.evaluate(() => {
      // Cari element yang mungkin berisi link download
      const elements = document.querySelectorAll('a[href*="download"], a[href*="dlink"], a[href*="file"]');
      const links = [];
      elements.forEach(el => {
        links.push({
          href: el.href,
          text: el.textContent,
          className: el.className,
        });
      });
      
      // Cari di console atau variable JS
      const jsVars = [];
      for (const key of Object.keys(window)) {
        try {
          const val = window[key];
          if (val && typeof val === 'object' && val.dlink) {
            jsVars.push({ name: key, dlink: val.dlink });
          }
          if (val && typeof val === 'object' && val.url && val.url.includes('download')) {
            jsVars.push({ name: key, url: val.url });
          }
        } catch {}
      }
      
      // Cari di content HTML
      const html = document.documentElement.innerHTML;
      const dlinkPatterns = [
        /"dlink"\s*:\s*"([^"]+)"/gi,
        /"download_url"\s*:\s*"([^"]+)"/gi,
        /"url"\s*:\s*"([^"]*(?:download|dlink)[^"]*)"/gi,
      ];
      
      const foundInHtml = [];
      for (const pattern of dlinkPatterns) {
        let match;
        while ((match = pattern.exec(html)) !== null) {
          foundInHtml.push(match[1]);
        }
      }
      
      return { links, jsVars, foundInHtml };
    });
    
    log('Links found:', pageData.links.length);
    log('JS vars found:', pageData.jsVars.length);
    log('HTML patterns found:', pageData.foundInHtml.length);
    
    // Coba klik tombol download jika ada
    log('Mencoba klik tombol download...');
    const downloadButtons = await page.$$('a[href*="download"], button:has-text("Download"), .download-btn, .download-button');
    log('Download buttons found:', downloadButtons.length);
    
    let downloadStarted = false;
    if (downloadButtons.length > 0) {
      for (const btn of downloadButtons.slice(0, 3)) {
        try {
          await btn.click();
          log('Klik tombol download berhasil');
          downloadStarted = true;
          break;
        } catch (e) {
          log('Gagal klik tombol:', e.message);
        }
      }
    }
    
    if (downloadStarted) {
      // Tunggu sebentar untuk proses download
      await page.waitForTimeout(CONFIG.DOWNLOAD_WAIT);
    }
    
    // Cari di network traffic
    log('Mencari download URL dari network traffic...');
    
    let foundUrl = null;
    for (const captured of networkUrls) {
      try {
        const data = JSON.parse(captured.body);
        if (data.dlink && data.dlink.startsWith('http')) {
          log('Ditemukan dlink di network:', data.dlink.slice(0, 100));
          foundUrl = data.dlink;
          break;
        }
        if (data.url && data.url.startsWith('http')) {
          log('Ditemukan url di network:', data.url.slice(0, 100));
          foundUrl = data.url;
          break;
        }
        // Beberapa format return di nested
        if (data.data && data.data.dlink) {
          foundUrl = data.data.dlink;
          break;
        }
      } catch {
        // Bukan JSON, cek apakah URL langsung
        if (captured.url.includes('download') && captured.url.includes('fsid')) {
          foundUrl = captured.url;
          break;
        }
      }
    }
    
    // Bagikan hasil
    if (foundUrl) {
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: foundUrl,
        filename: 'file_from_browser',
        cookie_file: cookieFile,
        referer: currentUrl,
        method: 'browser_interaction',
      }));
    } else if (pageData.foundInHtml.length > 0) {
      // Ditemukan di HTML tapi tidak di network
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: pageData.foundInHtml[0],
        filename: 'file_from_html',
        cookie_file: cookieFile,
        referer: currentUrl,
        method: 'html_parse',
      }));
    } else if (pageData.jsVars.length > 0) {
      // Ditemukan di JS variable
      const jsVar = pageData.jsVars[0];
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: jsVar.dlink || jsVar.url,
        filename: 'file_from_js',
        cookie_file: cookieFile,
        referer: currentUrl,
        method: 'js_variable',
      }));
    } else {
      // Tidak ada yang ditemukan
      console.log(JSON.stringify({
        ok: false,
        error: 'Tidak dapat download URL. Halaman mungkin memerlukan interaksi manual atau verifikasi tambahan.',
        debug: {
          buttons_found: downloadButtons.length,
          links_found: pageData.links.length,
          js_vars_found: pageData.jsVars.length,
          html_patterns: pageData.foundInHtml.length,
          network_captures: networkUrls.length,
          current_url: currentUrl,
        }
      }));
      process.exit(1);
    }
    
  } catch (err) {
    log('ERROR:', err.message);
    if (browser) {
      try { await browser.close(); } catch {}
    }
    console.log(JSON.stringify({ 
      ok: false, 
      error: String(err.message || err) 
    }));
    process.exit(1);
  } finally {
    if (browser) {
      try { await browser.close(); } catch {}
    }
  }
}

main();
