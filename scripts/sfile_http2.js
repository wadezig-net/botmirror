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

const FETCH_HDR = {
  'accept': '*/*',
  'accept-language': 'en-US,en;q=0.9',
  'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
  'sec-ch-ua-mobile': '?0',
  'sec-ch-ua-platform': '"Windows"',
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

function genPid() {
  const seed = [UA, 'Windows NT 10.0', 'Win64', 'x64', 'Chrome/131.0.0.0', Date.now()].join('|');
  return crypto.createHash('md5').update(seed).digest('hex')
    + crypto.createHash('sha1').update(seed).digest('hex').slice(0, 12);
}

function get(session, host, urlPath, extra = {}, isDoc = false) {
  return new Promise((resolve, reject) => {
    const req = session.request({
      ':method': 'GET', ':path': urlPath, ':authority': host, ':scheme': 'https',
      'user-agent': UA,
      ...(isDoc ? DOC_HDR : FETCH_HDR),
      ...extra,
    });
    const chunks = [];
    req.on('response', (hdrs) => {
      parseCookies(hdrs);
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        const raw = Buffer.concat(chunks);
        let body = raw.toString('utf8');
        const enc = hdrs['content-encoding'];
        if (enc === 'br') body = zlib.brotliDecompressSync(raw).toString('utf8');
        else if (enc === 'gzip') body = zlib.gunzipSync(raw).toString('utf8');
        resolve({ status: Number(hdrs[':status']), headers: hdrs, body, raw });
      });
    });
    req.on('error', reject);
    req.end();
  });
}

/**
 * Extract final direct download URL (CDN URL) for given sfile share URL.
 * Returns { download_url, direct_url, bypassed_wait, cdn_url } or null on failure.
 */
async function resolveDownload(shareUrl) {
  const sharePath = new URL(shareUrl).pathname;
  const session = newSession('sfile.co');

  try {
    // Step 1: share page
    const r1 = await get(session, 'sfile.co', sharePath, {}, true);

    // Step 2: download page (path + query from data-dw-url)
    const dlMatch = r1.body.match(/data-dw-url="([^"]+)"/);
    if (!dlMatch) throw new Error('No data-dw-url found');
    const dlUrl = dlMatch[1];
    const dlPath = new URL(dlUrl).pathname + new URL(dlUrl).search;
    const r2 = await get(session, 'sfile.co', dlPath, {
      referer: shareUrl,
      cookie: cookieHeader(),
    }, true);

    // Step 3: set _pid fingerprint and re-fetch download page (server attaches cookies needed for download)
    COOKIE_JAR['_pid'] = genPid();
    await get(session, 'sfile.co', dlPath, {
      referer: shareUrl,
      cookie: cookieHeader(),
    }, true);

    // Step 4: extract data-direct-download and decode HTML entity
    const btnMatch = r2.body.match(/data-direct-download="([^"]+)"/);
    if (!btnMatch) throw new Error('No data-direct-download found');
    const AMP_ENTITY = String.fromCharCode(0x26) + 'amp;';
    const directUrl = btnMatch[1].replace(new RegExp(AMP_ENTITY, 'g'), '&');

    // Step 5: hit direct download URL → 302 to CDN
    const u = new URL(directUrl);
    const r3 = await get(session, u.hostname, u.pathname + u.search, {
      referer: shareUrl,
      cookie: cookieHeader(),
    });

    let cdnUrl = null;
    if (r3.status === 302 && r3.headers.location) {
      cdnUrl = r3.headers.location;
    }

    return {
      download_url: dlUrl,
      direct_url: directUrl,
      cdn_url: cdnUrl,
      bypassed_wait: true,
    };
  } finally {
    session.close();
  }
}

/**
 * Download the actual file from CDN URL and save to disk.
 */
async function downloadFromCdn(cdnUrl, outputPath, shareReferer = 'https://sfile.mobi/') {
  const u = new URL(cdnUrl);
  const session = newSession(u.hostname);

  try {
    const r = await get(session, u.hostname, u.pathname + u.search, {
      referer: shareReferer,
    });

    const ct = r.headers['content-type'] || '';
    if (r.status !== 200 || ct.includes('text/html')) {
      return { success: false, status: r.status, content_type: ct, error: 'CDN returned HTML instead of file' };
    }

    fs.writeFileSync(outputPath, r.raw);

    // Pick filename from Content-Disposition if available
    let filename = path.basename(outputPath);
    const cd = r.headers['content-disposition'];
    if (cd) {
      const fn = cd.match(/filename\*?=(?:UTF-8'')?["']?([^";'"]+)/i);
      if (fn) {
        try { filename = decodeURIComponent(fn[1]); } catch (_) { filename = fn[1]; }
      }
    }

    return {
      success: true,
      path: outputPath,
      filename,
      size: r.raw.length,
      content_type: ct,
    };
  } finally {
    session.close();
  }
}

// CLI mode:
//   node downloader.js resolve <shareUrl>           → JSON { download_url, direct_url, cdn_url, bypassed_wait }
//   node downloader.js download <shareUrl> <outFile> → JSON { success, path, size, filename, content_type, cdn_url }
//   node downloader.js cdn <cdnUrl> <outFile>        → JSON { success, ... } (just download from a known CDN URL)
async function main() {
  const [, , cmd, ...rest] = process.argv;
  try {
    if (cmd === 'resolve') {
      const [shareUrl] = rest;
      if (!shareUrl) throw new Error('Usage: node downloader.js resolve <shareUrl>');
      const r = await resolveDownload(shareUrl);
      console.log(JSON.stringify(r));
    } else if (cmd === 'download') {
      const [shareUrl, outFile] = rest;
      if (!shareUrl || !outFile) throw new Error('Usage: node downloader.js download <shareUrl> <outFile>');
      const r = await resolveDownload(shareUrl);
      if (!r.cdn_url) throw new Error('No CDN URL returned (token expired or invalid share URL)');
      const dl = await downloadFromCdn(r.cdn_url, outFile, shareUrl);
      console.log(JSON.stringify({ ...r, download: dl }));
    } else if (cmd === 'cdn') {
      const [cdnUrl, outFile] = rest;
      if (!cdnUrl || !outFile) throw new Error('Usage: node downloader.js cdn <cdnUrl> <outFile>');
      const dl = await downloadFromCdn(cdnUrl, outFile);
      console.log(JSON.stringify(dl));
    } else {
      console.error('Unknown command:', cmd);
      console.error('Commands:');
      console.error('  resolve <shareUrl>');
      console.error('  download <shareUrl> <outFile>');
      console.error('  cdn <cdnUrl> <outFile>');
      process.exit(2);
    }
  } catch (e) {
    console.error(e.message || e);
    process.exit(1);
  }
}

// Export for embedding from Python (via subprocess)
module.exports = { resolveDownload, downloadFromCdn };

if (require.main === module) main();
