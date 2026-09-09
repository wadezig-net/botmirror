const http2 = require('node:http2');
const tls = require('node:tls');
const zlib = require('node:zlib');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

const DOC_HDR = {
  'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
  'accept-language': 'en-US,en;q=0.9',
  'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
  'sec-ch-ua-mobile': '?0',
  'sec-ch-ua-platform': '"Windows"',
  'sec-fetch-dest': 'document',
  'sec-fetch-mode': 'navigate',
  'sec-fetch-site': 'none',
  'sec-fetch-user': '?1',
  'upgrade-insecure-requests': '1',
};

const API_HDR = {
  'accept': 'application/json, text/plain, */*',
  'accept-language': 'en-US,en;q=0.9',
  'sec-fetch-dest': 'empty',
  'sec-fetch-mode': 'cors',
  'sec-fetch-site': 'same-origin',
};

function newSession(host) {
  return http2.connect('https://' + host, {
    createConnection: () => tls.connect({
      host, port: 443, servername: host,
      ALPNProtocols: ['h2', 'http/1.1'],
      ciphers: 'TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA',
      minVersion: 'TLSv1.2', maxVersion: 'TLSv1.3',
    }),
  });
}

const COOKIE_JAR = {};

function parseCookies(h) {
  if (!h['set-cookie']) return;
  for (const line of (Array.isArray(h['set-cookie']) ? h['set-cookie'] : [h['set-cookie']])) {
    const part = line.split(';')[0].trim();
    if (part.includes('=')) {
      const [n, v] = part.split('=', 2);
      COOKIE_JAR[n.trim()] = v.trim();
    }
  }
}

const cookieHeader = () => Object.entries(COOKIE_JAR).map(([k, v]) => k + '=' + v).join('; ');

function genFingerprint() {
  const seed = [UA, 'Windows NT 10.0', 'Win64', 'x64', 'Chrome/131.0.0.0', Date.now(), Math.random().toString(36)].join('|');
  return crypto.createHash('md5').update(seed).digest('hex') + crypto.createHash('sha1').update(seed).digest('hex').slice(0, 12);
}

function get(session, host, urlPath, extra = {}, isDoc = false) {
  return new Promise((resolve, reject) => {
    try {
      const req = session.request({
        ':method': 'GET', ':path': urlPath, ':authority': host, ':scheme': 'https',
        'user-agent': UA,
        ...(isDoc ? DOC_HDR : API_HDR),
        ...extra,
      });
      const chunks = [];
      let settled = false;
      
      const cleanup = () => {
        if (settled) return;
        settled = true;
        req.removeAllListeners('error');
      };
      
      req.on('response', (hdrs) => {
        cleanup();
        parseCookies(hdrs);
        req.on('data', (c) => chunks.push(c));
        req.on('end', () => {
          if (settled) return;
          settled = true;
          const raw = Buffer.concat(chunks);
          let body = raw.toString('utf8');
          const enc = hdrs['content-encoding'];
          if (enc === 'br') {
            try { body = zlib.brotliDecompressSync(raw).toString('utf8'); }
            catch { body = raw.toString('utf8'); }
          } else if (enc === 'gzip') {
            try { body = zlib.gunzipSync(raw).toString('utf8'); }
            catch { body = raw.toString('utf8'); }
          }
          resolve({ status: Number(hdrs[':status']), headers: hdrs, body, raw });
        });
        req.on('error', () => {}); // ignore errors after response
      });
      
      req.on('error', async (err) => {
        cleanup();
        // Fallback ke HTTP/1.1 via fetch
        log('HTTP/2 error, fallback ke fetch:', err.message);
        try {
          const url = new URL(urlPath, `https://${host}/`);
          const resp = await fetch(url.toString(), {
            method: 'GET',
            headers: {
              'User-Agent': UA,
              'Accept': 'application/json, text/plain, */*',
              'Accept-Language': 'en-US,en;q=0.9',
              'Referer': extra.referer || `https://${host}/`,
              ...extra,
            },
          });
          const text = await resp.text();
          resolve({ 
            status: resp.status, 
            headers: Object.fromEntries(resp.headers.entries()), 
            body: text, 
            raw: new TextEncoder().encode(text) 
          });
        } catch (e2) {
          log('Fetch fallback gagal:', e2.message);
          reject(err); // reject dengan error asli jika fallback gagal
        }
      });
      
      req.end();
    } catch (err) {
      reject(err);
    }
  });
}

async function extractSurlFromPage(pageUrl) {
  // Extract surl from various Terabox URL formats
  try {
    const url = new URL(pageUrl);
    const surlMatch = pageUrl.match(/[?&]surl=([^&]+)/) || pageUrl.match(/\/s\/([^/?]+)/);
    if (surlMatch) return surlMatch[1];
    
    // Check path segments
    const pathParts = url.pathname.split('/').filter(Boolean);
    for (const part of pathParts) {
      if (part.length >= 6 && /^[a-zA-Z0-9_-]+$/.test(part)) {
        // Possible surl
        return part;
      }
    }
  } catch (e) {
    console.error('Error extracting surl:', e.message);
  }
  return null;
}

async function resolveTerabox(shareUrl) {
  const parsedUrl = new URL(shareUrl);
  const host = parsedUrl.hostname;
  const session = newSession(host);
  
  try {
    // Step 1: Browse the share page to get cookies
    const pagePath = parsedUrl.pathname + parsedUrl.search;
    log('Step 1: Fetching share page...');
    const r1 = await get(session, host, pagePath, {}, true);
    log('Page status:', r1.status);
    
    // Step 2: Extract surl
    const surl = extractSurlFromPage(shareUrl) || extractSurlFromPage(r1.body);
    if (!surl) {
      throw new Error('Tidak bisa ekstrak surl dari URL');
    }
    log('Extracted surl:', surl);
    
    // Step 3: Call API to list share contents
    const referer = `https://${host}/sharing/link?surl=${surl}`;
    const apiParams = new URLSearchParams({
      app_id: '250528',
      channel: 'chunlei',
      web: '1',
      clienttype: '0',
      shorturl: surl,
      root: '1',
    });
    
    log('Step 2: Calling API...');
    const r2 = await get(session, host, `/share/list?${apiParams}`, {
      'cookie': cookieHeader(),
      'referer': referer,
    });
    log('API status:', r2.status);
    
    let apiData;
    try {
      apiData = JSON.parse(r2.body);
    } catch (e) {
      throw new Error('Respons API tidak valid: ' + r2.body.slice(0, 200));
    }
    
    log('API response:', JSON.stringify(apiData).slice(0, 300));
    
    if (apiData.errno !== 0 && apiData.errno !== undefined) {
      const errorMap = {
        105: 'Link tidak ditemukan / expired',
        '-6': 'Butuh login (link privat)',
        '-7': 'Link tidak valid',
        103: 'Akses ditolak',
        104: 'Parameter tidak valid',
      };
      const msg = errorMap[apiData.errno] || `Error ${apiData.errno}`;
      throw new Error(`Terabox API error ${apiData.errno}: ${msg}`);
    }
    
    if (!apiData.list || !Array.isArray(apiData.list)) {
      throw new Error('Respons API tidak menyertakan list file');
    }
    
    const list = apiData.list;
    log('Jumlah item:', list.length);
    
    // Handle single file
    if (list.length === 1 && String(list[0].isdir) !== '1') {
      const item = list[0];
      let dlink = item.dlink || item.dlink_url || item.download_url;
      
      if (!dlink) {
        // Try /share/download endpoint
        log('Mencoba endpoint /share/download...');
        const dlParams = new URLSearchParams({
          app_id: '250528',
          channel: 'chunlei',
          web: '1',
          clienttype: '0',
          shorturl: surl,
          fsid: String(item.fs_id || item.fsid),
          rt: '1',
          type: 'dlink',
        });
        
        const r3 = await get(session, host, `/share/download?${dlParams}`, {
          'cookie': cookieHeader(),
          'referer': referer,
        });
        
        try {
          const dlData = JSON.parse(r3.body);
          if (dlData.errno === 0 && dlData.dlink) {
            dlink = dlData.dlink;
          }
        } catch (e) {}
      }
      
      if (!dlink) {
        throw new Error('File single tidak bisa di-resolve. Link mungkin terproteksi (butuh login).');
      }
      
      return {
        ok: true,
        type: 'single',
        direct_url: dlink,
        filename: item.server_filename || item.name || 'terabox_file',
        cookie_file: path.join(process.argv[3], 'terabox_cookies.json'),
        referer: referer,
      };
    }
    
    // Handle folder
    if (list.length > 0) {
      const collected = [];
      
      for (const item of list) {
        if (String(item.isdir) === '1') {
          // Recursive - simplified
          log('Folder ditemukan, butuh rekursi (dilakukan terpisah)');
        } else {
          const dlink = item.dlink || item.dlink_url || item.download_url;
          if (dlink) {
            collected.push({ url: dlink, filename: item.server_filename || item.name });
          } else {
            // Try download endpoint
            const dlParams = new URLSearchParams({
              app_id: '250528',
              channel: 'chunlei',
              web: '1',
              clienttype: '0',
              shorturl: surl,
              fsid: String(item.fs_id || item.fsid),
              rt: '1',
              type: 'dlink',
            });
            
            const r4 = await get(session, host, `/share/download?${dlParams}`, {
              'cookie': cookieHeader(),
              'referer': referer,
            });
            
            try {
              const dlData = JSON.parse(r4.body);
              if (dlData.errno === 0 && dlData.dlink) {
                collected.push({ url: dlData.dlink, filename: item.server_filename || item.name });
              }
            } catch (e) {}
          }
        }
      }
      
      if (collected.length === 0) {
        throw new Error('Tidak ada file yang bisa di-resolve dari folder ini');
      }
      
      // Save cookies
      const cookieFile = path.join(process.argv[3], 'terabox_cookies.json');
      fs.writeFileSync(cookieFile, JSON.stringify(COOKIE_JAR));
      
      return {
        ok: true,
        type: 'folder',
        files: collected,
        cookie_file: cookieFile,
        referer: referer,
      };
    }
    
    throw new Error(' Tidak ada item di share link');
    
  } finally {
    session.close();
  }
}

function log(...args) {
  console.error('[http2]', new Date().toISOString(), ...args);
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: 'Usage: node terabox_http2.js <url> <work_dir>' }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });
  
  try {
    const result = await resolveTerabox(url);
    console.log(JSON.stringify(result));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err.message || err) }));
    process.exitCode = 1;
  }
}

main();
