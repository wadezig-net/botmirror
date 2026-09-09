/**
 * Terabox Debug - Detailed interaction tracking
 * 
 * Bangun untuk memahami apa yang terjadi setelah klik download button
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

function log(...args) {
  console.error('[debug]', new Date().toISOString(), ...args);
}

async function main() {
  const url = 'https://www.terabox.com/wap/share/filelist?surl=DBLY-Zm-5PWNsdXsMjuU0A';
  const workDir = '/tmp/terabox_debug_detailed';
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
  log('Memulai debug interaction...');
  
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  
  const context = await browser.newContext({
    userAgent: UA,
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
  page.on('dialog', async (dialog) => {
    log('DIALOG:', dialog.message());
    log('DIALOG type:', dialog.type());
    await dialog.dismiss().catch(() => {});
  });
  
  // Track semua network
  const allNetwork = [];
  page.on('response', async (response) => {
    const resUrl = response.url();
    const status = response.status();
    
    // Log semua response yang interesting
    if (resUrl.includes('download') || 
        resUrl.includes('verify') || 
        resUrl.includes('vcode') ||
        resUrl.includes('popup') ||
        resUrl.includes('dialog') ||
        resUrl.includes('captcha') ||
        resUrl.includes('confirm') ||
        status >= 300) {
      try {
        const body = await response.text();
        allNetwork.push({
          url: resUrl,
          status,
          body: body.slice(0, 2000),
          headers: Object.fromEntries(response.headers.entries()),
        });
        log('NETWORK:', resUrl.slice(0, 150), status);
        if (body.length > 0) {
          log('  BODY:', body.slice(0, 500));
        }
      } catch {}
    }
  });
  
  // Track console messages
  page.on('console', (msg) => {
    log('CONSOLE:', msg.type(), msg.text());
  });
  
  // Track page events
  page.on('pageerror', (error) => {
    log('PAGE ERROR:', error.message);
  });
  
  // Track popup windows
  page.on('popup', async (popup) => {
    log('POPUP opened:', popup.url());
    const popupPage = popup;
    popupPage.on('console', (msg) => log('POPUP CONSOLE:', msg.text()));
    popupPage.on('response', async (res) => {
      log('POPUP NETWORK:', res.url(), res.status());
      try {
        const body = await res.text();
        if (body.length > 0 && body.length < 5000) {
          log('  BODY:', body.slice(0, 500));
        }
      } catch {}
    });
  });
  
  // Track download events
  page.on('download', (download) => {
    log('DOWNLOAD detected:', download.suggestedFilename(), download.url());
  });
  
  // Navigate
  log('Navigasi ke halaman...');
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(5000);
  
  // Dapatkan cookies
  const cookies = await context.cookies();
  log('Cookies:', cookies.map(c => c.name));
  fs.writeFileSync(path.join(workDir, 'cookies.json'), JSON.stringify(cookies));
  
  // Cek halaman saat ini
  log('Current URL:', page.url());
  
  // Evaluasi halaman untuk mencari struktur
  log('Mengevaluasi halaman...');
  const pageInfo = await page.evaluate(() => {
    return {
      title: document.title,
      url: location.href,
      // Cari element yang mungkin berhubungan dengan download
      downloadElements: Array.from(document.querySelectorAll('[class*="download"], [class*="Download"], [data-vc-name*="download"]'))
        .map(el => ({
          tag: el.tagName,
          class: el.className,
          id: el.id,
          text: el.textContent?.slice(0, 100),
          href: el.href,
          'data-*': Object.keys(el.dataset).reduce((acc, key) => ({ ...acc, [key]: el.dataset[key] }), {}),
        })),
      // Cari element yang berhubungan dengan file list
      fileElements: Array.from(document.querySelectorAll('[class*="file"], [class*="item"], [data-type*="file"]'))
        .slice(0, 5)
        .map(el => ({
          tag: el.tagName,
          class: el.className?.slice(0, 100),
          text: el.textContent?.slice(0, 100),
          attributes: Array.from(el.attributes).slice(0, 10).map(a => `${a.name}="${a.value}"`).join(', '),
        })),
      // Cari popup/modal yang mungkin ada
      modalElements: Array.from(document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="popup"], [class*="verify"]'))
        .map(el => ({
          tag: el.tagName,
          class: el.className?.slice(0, 100),
          visible: el.offsetParent !== null,
          text: el.textContent?.slice(0, 200),
        })),
    };
  });
  
  log('Page info:', JSON.stringify(pageInfo, null, 2).slice(0, 3000));
  
  // Cari tombol download dan klik
  log('Mencari tombol download...');
  const downloadBtns = await page.$$('a[class*="download"], button[class*="download"], a:has-text("Download"), button:has-text("Download")');
  log('Download buttons:', downloadBtns.length);
  
  if (downloadBtns.length > 0) {
    log('Klik tombol download pertama...');
    await downloadBtns[0].click();
    await page.waitForTimeout(2000);
    
    log('Setelah klik - Current URL:', page.url());
    
    // Cek apakah ada popup/modal baru
    const modalAfterClick = await page.evaluate(() => {
      return {
        modals: Array.from(document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="popup"], [class*="verify"], [class*="vcode"]'))
          .map(el => ({
            tag: el.tagName,
            class: el.className?.slice(0, 100),
            visible: el.offsetParent !== null,
            text: el.textContent?.slice(0, 300),
          })),
        alerts: Array.from(document.querySelectorAll('.ant-alert, [class*="alert"]'))
          .map(el => el.textContent?.slice(0, 200)),
      };
    });
    
    log('Modal after click:', JSON.stringify(modalAfterClick, null, 2).slice(0, 2000));
    
    // Jika ada modal verifikasi, coba interaksinya
    if (modalAfterClick.modals.length > 0) {
      for (const modal of modalAfterClick.modals) {
        if (modal.visible && modal.class.includes('verify')) {
          log('Temukan modal verify, mencoba interaksi...');
          
          // Cari tombol di dalam modal
          const modalElement = await page.$(`[class*="${modal.class.split(' ')[0]}"]`);
          if (modalElement) {
            // Cari tombol confirm/OK/continue
            const confirmBtns = await modalElement.$$('button:has-text("Confirm"), button:has-text("OK"), button:has-text("Continue"), button:has-text("Verify"), button:has-text("Send"), a:has-text("Confirm")');
            log('Confirm buttons in modal:', confirmBtns.length);
            
            for (const btn of confirmBtns) {
              await btn.click();
              log('Klik confirm button');
              await page.waitForTimeout(3000);
              break;
            }
            
            // Jika tidak ada tombol, coba tekan Enter
            if (confirmBtns.length === 0) {
              await page.keyboard.press('Enter');
              log('Tekan Enter');
              await page.waitForTimeout(3000);
            }
          }
        }
      }
    }
    
    // Tunggu lebih lanjut
    await page.waitForTimeout(5000);
    
    log('Final URL:', page.url());
  }
  
  // Simpan semua network trace
  fs.writeFileSync(
    path.join(workDir, 'network_trace.json'),
    JSON.stringify(allNetwork, null, 2)
  );
  
  log('Selesai. Total network events:', allNetwork.length);
  
  await browser.close();
}

main().catch(e => {
  console.error('Fatal error:', e);
  process.exit(1);
});
