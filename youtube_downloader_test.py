#!/usr/bin/env python3
import sys
import re
import argparse
import unittest
import json
import os
import tempfile
import subprocess
import math
import shutil
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote

# Try yt_dlp first, fallback to youtube_dl
try:
    from yt_dlp import YoutubeDL
    using_module = 'yt_dlp'
except ImportError:
    from youtube_dl import YoutubeDL
    using_module = 'youtube_dl'

# Directory for final segments
DOWNLOAD_DIR = os.path.abspath('downloads')
if not os.path.isdir(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Core Functions ---

def parse_video_id(url: str) -> str:
    "Extract YouTube video ID from URL."  
    patterns = [r"v=([0-9A-Za-z_-]{11})", r"youtu\.be/([0-9A-Za-z_-]{11})", r"embed/([0-9A-Za-z_-]{11})"]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid YouTube URL: {url}")


def get_video_info(url: str) -> dict:
    "Return title, thumbnail and available qualities."  
    opts = {'quiet': True, 'skip_download': True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        'title': info.get('title'),
        'thumbnail_url': info.get('thumbnail', ''),
        'qualities': sorted({f"{fmt.get('height')}p" for fmt in info.get('formats',[]) if fmt.get('height')}),
        'module': using_module
    }


def download_and_merge(url: str, resolution: str) -> str:
    "Download and merge video+audio; return filepath."  
    height = int(resolution.rstrip('p'))
    fmt = f"bestvideo[height<={height}]+bestaudio/best"
    tmpdir = tempfile.mkdtemp(prefix='ytdl_')
    outtmpl = os.path.join(tmpdir, '%(id)s.%(ext)s')
    opts = {'quiet': True, 'format': fmt, 'outtmpl': outtmpl, 'merge_output_format': 'mp4'}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vid = info.get('id')
    path = os.path.join(tmpdir, f"{vid}.mp4")
    if not os.path.exists(path):
        files = os.listdir(tmpdir)
        if files: path = os.path.join(tmpdir, files[0])
    return path


def split_and_resize(filepath: str, orientation: str = 'vertical') -> list:
    "Split video <=60s segments, crop/rotate based on orientation, resize, save to DOWNLOAD_DIR."  
    # Probe duration
    proc = subprocess.run([
        'ffprobe','-v','error','-show_entries','format=duration',
        '-of','default=noprint_wrappers=1:nokey=1', filepath
    ], capture_output=True, text=True)
    try:
        duration = float(proc.stdout.strip())
    except:
        duration = 0.0
    # Probe resolution
    proc2 = subprocess.run([
        'ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=width,height','-of','csv=p=0', filepath
    ], capture_output=True, text=True)
    try:
        in_w, in_h = map(int, proc2.stdout.strip().split(','))
    except:
        in_w, in_h = 1280, 720
    # Determine segments
    MAX_LEN = 60.0
    num_segs = max(1, math.ceil(duration / MAX_LEN))
    seg_len = duration / num_segs if num_segs > 0 else duration
    base = os.path.splitext(os.path.basename(filepath))[0]
    tmpdir = os.path.dirname(filepath)
    seg_paths = []
    print(f"Duration {duration:.2f}s => {num_segs} segments (~{seg_len:.2f}s each), orientation={orientation}")
    for i in range(num_segs):
        start = round(i * seg_len)
        end = round(min((i + 1) * seg_len, duration))
        # Build filter
        if orientation == 'vertical':
            crop_h = in_h; crop_w = int(in_h * 9/16)
            crop_x = (in_w - crop_w)//2; crop_y = (in_h - crop_h)//2
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=720:1280"
        else:
            vf = "transpose=1,scale=1280:720"
        inter = os.path.join(tmpdir, f"{base}_{start}_{end}.mp4")
        cmd = [
            'ffmpeg','-y','-i',filepath,
            '-ss',str(start),'-to',str(end),
            '-vf',vf,
            '-preset','ultrafast','-c:a','copy',inter
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final = os.path.join(DOWNLOAD_DIR, os.path.basename(inter))
        shutil.move(inter, final)
        print(f"Segment {i+1}/{num_segs}: {start}-{end}s -> {final}")
        seg_paths.append(final)
    print(f"Completed {len(seg_paths)} segments in {DOWNLOAD_DIR}")
    return seg_paths

# --- HTTP Handler ---
class YouTubeHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200, content_type='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if extra:
            for k, v in extra.items(): self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        if self.path == '/fetch':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                info = get_video_info(data.get('url', ''))
                self._set_headers()
                self.wfile.write(json.dumps(info).encode())
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        else:
            self.send_error(404)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == '/':
            self._set_headers(200, 'text/html')
            self.wfile.write(INDEX_HTML.encode())
            return
        if p.path == '/split':
            params = parse_qs(p.query)
            url = params.get('url', [''])[0]
            res = params.get('resolution', [''])[0]
            orient = params.get('orientation', ['vertical'])[0]
            try:
                fp = download_and_merge(unquote(url), unquote(res))
                segs = split_and_resize(fp, orient)
                host = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
                items = [{'name': os.path.basename(s), 'url': f"{host}/segment?path={quote(s)}"} for s in segs]
                self._set_headers()
                self.wfile.write(json.dumps(items).encode())
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
        if p.path == '/segment':
            params = parse_qs(p.query)
            path = unquote(params.get('path', [''])[0])
            if os.path.exists(path):
                size = os.path.getsize(path); name = os.path.basename(path)
                hdr = {'Content-Length': str(size), 'Content-Disposition': f'attachment; filename="{name}"'}
                self._set_headers(200, 'video/mp4', hdr)
                with open(path, 'rb') as f:
                    while chunk := f.read(1024 * 512): self.wfile.write(chunk)
            else:
                self.send_error(404)
            return
        self.send_error(404)

# --- HTML Template ---
INDEX_HTML = '''<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube Downloader & Splitter</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-5">
  <h1 class="mb-4">YouTube Downloader & Splitter</h1>
  <div class="input-group mb-3">
    <input id="url" class="form-control" placeholder="YouTube URL">
    <select id="qualities" class="form-select ms-2"></select>
    <select id="orientation" class="form-select ms-2">
      <option value="vertical" selected>Vertical (9:16)</option>
      <option value="horizontal">Horizontal (rotate 90°)</option>
    </select>
    <button id="fetch" class="btn btn-primary ms-2">Fetch</button>
    <button id="split" class="btn btn-warning ms-2 d-none">Split</button>
  </div>
  <div id="info" class="d-none">
    <h3 id="title"></h3>
    <p id="module" class="text-muted"></p>
    <img id="thumbnail" class="img-fluid mb-3" src="" alt="Thumbnail">
  </div>
  <div id="segments" class="row"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
let currentURL = '';
document.getElementById('fetch').addEventListener('click',async()=>{
  const u=document.getElementById('url').value;currentURL=u;
  const r=await fetch('/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u})});
  const d=await r.json();if(d.error) return alert(d.error);
  document.getElementById('info').classList.remove('d-none');
  document.getElementById('title').textContent=d.title;
  document.getElementById('module').textContent=`Using: ${d.module}`;
  document.getElementById('thumbnail').src=d.thumbnail_url;
  const qs=document.getElementById('qualities');qs.innerHTML='';d.qualities.forEach(q=>qs.append(new Option(q,q)));
  document.getElementById('split').classList.remove('d-none');
});
document.getElementById('split').addEventListener('click',async()=>{
  const res=document.getElementById('qualities').value;
  const orient=document.getElementById('orientation').value;
  const r=await fetch(`/split?url=${encodeURIComponent(currentURL)}&resolution=${res}&orientation=${orient}`);
  const items=await r.json();
  const cont=document.getElementById('segments');cont.innerHTML='';
  items.forEach(it=>{const col=document.createElement('div');col.className='col-md-6 mb-3';col.innerHTML=`<div class="card"><div class="card-body"><h5 class="card-title">${it.name}</h5><video class="w-100 mb-2"controls src="${it.url}"></video><a href="${it.url}"class="btn btn-sm btn-success">Download</a></div></div>`;cont.appendChild(col);});
});
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
    parser=argparse.ArgumentParser(description="YT Downloader & Splitter")
    parser.add_argument('--test',action='store_true');parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=5111)
    args=parser.parse_args()
    if args.test:
        unittest.main(argv=[sys.argv[0]])
    else:
        try:
            srv=ThreadingHTTPServer((args.host,args.port),YouTubeHandler)
            print(f"Server on {args.host}:{args.port}")
        except OSError:
            print("Port busy, using ephemeral",file=sys.stderr)
            srv=ThreadingHTTPServer((args.host,0),YouTubeHandler)
            print(f"Server on {args.host}:{srv.server_address[1]}")
        srv.serve_forever()

if __name__=='__main__':
    main()
