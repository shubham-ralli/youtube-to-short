#!/usr/bin/env python3
import sys
import re
import argparse
import unittest
import json
import os
import tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# Try yt_dlp first, fallback to youtube_dl
try:
    from yt_dlp import YoutubeDL
    using_module = 'yt_dlp'
except ImportError:
    from youtube_dl import YoutubeDL
    using_module = 'youtube_dl'

# --- Core Functions ---
def parse_video_id(url: str) -> str:
    patterns = [r"v=([0-9A-Za-z_-]{11})", r"youtu\.be/([0-9A-Za-z_-]{11})", r"embed/([0-9A-Za-z_-]{11})"]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid YouTube URL: {url}")


def get_video_info(url: str) -> dict:
    ydl_opts = {'quiet': True, 'skip_download': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        'title': info.get('title'),
        'thumbnail_url': info.get('thumbnail', ''),
        'qualities': sorted({f"{fmt.get('height')}p" for fmt in info.get('formats', []) if fmt.get('height')}),
        'module': using_module
    }


def download_and_merge(url: str, resolution: str) -> str:
    height = int(resolution.rstrip('p'))
    fmt_select = f"bestvideo[height<={height}]+bestaudio/best"
    tmpdir = tempfile.mkdtemp(prefix='ytdl_')
    outtmpl = os.path.join(tmpdir, '%(id)s.%(ext)s')
    ydl_opts = {'quiet': True, 'format': fmt_select, 'outtmpl': outtmpl, 'merge_output_format': 'mp4'}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vid = info.get('id')
    path = os.path.join(tmpdir, f"{vid}.mp4")
    if not os.path.exists(path):
        files = os.listdir(tmpdir)
        if files:
            path = os.path.join(tmpdir, files[0])
    return path

# --- HTTP Handler ---
class YouTubeHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200, content_type='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if extra:
            for k, v in extra.items(): self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        if self.path != '/fetch':
            self.send_error(404); return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            info = get_video_info(data.get('url',''))
            self._set_headers()
            self.wfile.write(json.dumps(info).encode())
        except Exception as e:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            self._set_headers(200, 'text/html')
            self.wfile.write(INDEX_HTML.encode())
            return
        if parsed.path == '/download':
            params = parse_qs(parsed.query)
            url = params.get('url',[''])[0]
            res = params.get('resolution',[''])[0]
            try:
                filepath = download_and_merge(unquote(url), unquote(res))
                size = os.path.getsize(filepath)
                name = os.path.basename(filepath)
                extra = {
                    'Content-Length': str(size),
                    'Content-Disposition': f'attachment; filename="{name}"'
                }
                self._set_headers(200, 'video/mp4', extra)
                with open(filepath, 'rb') as f:
                    while chunk := f.read(1024*512):
                        self.wfile.write(chunk)
                os.remove(filepath)
                os.rmdir(os.path.dirname(filepath))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
        self.send_error(404)

# --- HTML Template with Bootstrap 5 ---
INDEX_HTML = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YouTube Downloader</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-5">
  <h1 class="mb-4">YouTube Downloader</h1>
  <div class="input-group mb-3">
    <input id="url" type="text" class="form-control" placeholder="Enter YouTube URL">
    <button id="fetch" class="btn btn-primary">Fetch Info</button>
  </div>
  <div id="info" class="d-none">
    <h3 id="title"></h3>
    <p id="module" class="text-muted"></p>
    <img id="thumbnail" class="img-fluid mb-3" src="" alt="Thumbnail">
    <div class="mb-3 d-flex">
      <select id="qualities" class="form-select me-2"></select>
      <button id="download" class="btn btn-success">Download</button>
    </div>
    <div id="progressContainer" class="mb-2 d-none">
      <div class="progress">
        <div id="progressBar" class="progress-bar" role="progressbar" style="width: 0%;"></div>
      </div>
    </div>
    <div id="stats" class="small text-secondary d-none"></div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const fmtTime = s => new Date(s*1000).toISOString().substr(11,8);
let total=0, start=0;
document.getElementById('fetch').onclick = () => {
  fetch('/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:document.getElementById('url').value})})
    .then(r=>r.json()).then(d=>{if(d.error){alert(d.error);return;}document.getElementById('info').classList.remove('d-none');document.getElementById('title').textContent=d.title;document.getElementById('module').textContent=`Using: ${d.module}`;document.getElementById('thumbnail').src=d.thumbnail_url;const sel=document.getElementById('qualities');sel.innerHTML='';d.qualities.forEach(q=>sel.append(new Option(q,q)));});
};
document.getElementById('download').onclick = () => {
  const url=encodeURIComponent(document.getElementById('url').value);
  const res=encodeURIComponent(document.getElementById('qualities').value);
  const bar=document.getElementById('progressBar');
  const wrap=document.getElementById('progressContainer');
  const stats=document.getElementById('stats');
  bar.style.width='0%';
  wrap.classList.remove('d-none');
  stats.classList.remove('d-none');
  stats.textContent='';
  const xhr=new XMLHttpRequest();
  xhr.open('GET',`/download?url=${url}&resolution=${res}`);
  xhr.responseType='blob';
  xhr.onreadystatechange=()=>{if(xhr.readyState===XMLHttpRequest.HEADERS_RECEIVED){total=parseInt(xhr.getResponseHeader('Content-Length'))||0;start=Date.now()/1000;stats.textContent=`File Size: ${(total/1048576).toFixed(2)} MB`;}};
  xhr.onprogress=e=>{if(total){const l=e.loaded,perc=(l/total*100).toFixed(1),elapsed=Date.now()/1000-start,eta=fmtTime(elapsed*(total/l-1)),speed=(l/1024/1024/elapsed).toFixed(2);bar.style.width=`${perc}%`;stats.textContent=`Downloaded: ${(l/1048576).toFixed(2)}/${(total/1048576).toFixed(2)} MB (${perc}%) at ${speed} MB/s ETA ${eta}`;}};
  xhr.onload=()=>{const blob=xhr.response,a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=document.getElementById('title').textContent+'.mp4';document.body.appendChild(a);a.click();document.body.removeChild(a);wrap.classList.add('d-none');stats.classList.add('d-none');};
  xhr.onerror=()=>{alert('Download failed');wrap.classList.add('d-none');stats.classList.add('d-none');};
  xhr.send();
};
</script>
</body>
</html>'''

# --- Tests ---
class TestParseVideoID(unittest.TestCase):
    def test_standard(self): self.assertEqual(parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_short(self): self.assertEqual(parse_video_id("https://youtu.be/dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_embed(self): self.assertEqual(parse_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_invalid(self):
        with self.assertRaises(ValueError): parse_video_id("not a url")

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser(description="YouTube Downloader Web Server")
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5111)
    args = parser.parse_args()
    if args.test:
        unittest.main(argv=[sys.argv[0]])
    else:
        try:
            server = ThreadingHTTPServer((args.host, args.port), YouTubeHandler)
            print(f"Server on {args.host}:{args.port}")
        except OSError:
            print("Port busy, using ephemeral", file=sys.stderr)
            server = ThreadingHTTPServer((args.host, 0), YouTubeHandler)
            print(f"Server on {args.host}:{server.server_address[1]}")
        server.serve_forever()

if __name__ == '__main__':
    main()
