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
    "Extract YouTube video ID from URL."
    patterns = [r"v=([0-9A-Za-z_-]{11})", r"youtu\.be/([0-9A-Za-z_-]{11})", r"embed/([0-9A-Za-z_-]{11})"]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid YouTube URL: {url}")


def get_video_info(url: str) -> dict:
    "Return title, thumbnail, available qualities."
    ydl_opts = {'quiet': True, 'skip_download': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        'title': info.get('title'),
        'thumbnail_url': info.get('thumbnail',''),
        'qualities': sorted({f"{fmt.get('height')}p" for fmt in info.get('formats',[]) if fmt.get('height')}),
        'module': using_module
    }


def download_and_merge(url: str, resolution: str) -> str:
    "Download+merge video+audio, return filepath."
    height = int(resolution.rstrip('p'))
    fmt = f"bestvideo[height<={height}]+bestaudio/best"
    tmpdir = tempfile.mkdtemp(prefix='ytdl_')
    outtmpl = os.path.join(tmpdir, '%(id)s.%(ext)s')
    opts = {'quiet':True,'format':fmt,'outtmpl':outtmpl,'merge_output_format':'mp4'}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vid=info.get('id'); path=os.path.join(tmpdir,f"{vid}.mp4")
    if not os.path.exists(path): files=os.listdir(tmpdir); path=os.path.join(tmpdir,files[0]) if files else path
    return path

# --- HTTP Handler ---
class YouTubeHandler(BaseHTTPRequestHandler):
    def _set_headers(self,status=200,content_type='application/json',extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        if extra:
            for k,v in extra.items(): self.send_header(k,v)
        self.end_headers()

    def do_POST(self):
        if self.path != '/fetch': self.send_error(404); return
        length=int(self.headers.get('Content-Length',0)); body=self.rfile.read(length)
        try:
            data=json.loads(body)
            info=get_video_info(data.get('url',''))
            self._set_headers(); self.wfile.write(json.dumps(info).encode())
        except Exception as e:
            self._set_headers(400); self.wfile.write(json.dumps({'error':str(e)}).encode())

    def do_GET(self):
        p=urlparse(self.path)
        if p.path=='/': self._set_headers(200,'text/html'); self.wfile.write(INDEX_HTML.encode()); return
        if p.path=='/download':
            params=parse_qs(p.query);
            url=params.get('url',[''])[0]; res=params.get('resolution',[''])[0]
            try:
                fp=download_and_merge(unquote(url),unquote(res)); size=os.path.getsize(fp); name=os.path.basename(fp)
                hdr={'Content-Length':str(size),'Content-Disposition':f'attachment; filename="{name}"'}
                self._set_headers(200,'video/mp4',hdr)
                with open(fp,'rb') as f:
                    while chunk:=f.read(1024*512): self.wfile.write(chunk)
                os.remove(fp); os.rmdir(os.path.dirname(fp))
            except Exception as e:
                self._set_headers(400); self.wfile.write(json.dumps({'error':str(e)}).encode())
            return
        self.send_error(404)

# --- HTML Template using Fetch Streaming ---
INDEX_HTML = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube Downloader</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-5">
  <h1 class="mb-4">YouTube Downloader</h1>
  <div class="input-group mb-3">
    <input id="url" class="form-control" placeholder="Enter YouTube URL">
    <button id="fetch" class="btn btn-primary">Fetch Info</button>
  </div>
  <div id="info" class="d-none">
    <h3 id="title"></h3><p id="module" class="text-muted"></p>
    <img id="thumbnail" class="img-fluid mb-3">
    <div class="d-flex mb-3">
      <select id="qualities" class="form-select me-2"></select>
      <button id="download" class="btn btn-success">Download</button>
    </div>
    <div id="progressContainer" class="d-none mb-2">
      <div class="progress"><div id="progressBar" class="progress-bar" style="width:0%"></div></div>
    </div>
    <ul id="stats" class="list-unstyled small text-secondary d-none">
      <li>File Size: <span id="stat-size"></span></li>
      <li>Speed: <span id="stat-speed"></span></li>
      <li>ETA: <span id="stat-eta"></span></li>
    </ul>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const fmtTime=s=>{const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60);return[h,m,sec].map(x=>x.toString().padStart(2,'0')).join(':');};
let total=0,start=0;
document.getElementById('fetch').addEventListener('click',async()=>{
  const url=document.getElementById('url').value;
  const res=await fetch('/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
  const d=await res.json(); if(d.error){alert(d.error);return;}
  document.getElementById('info').classList.remove('d-none');
  document.getElementById('title').textContent=d.title;
  document.getElementById('module').textContent=`Using: ${d.module}`;
  document.getElementById('thumbnail').src=d.thumbnail_url;
  const sel=document.getElementById('qualities'); sel.innerHTML=''; d.qualities.forEach(q=>sel.append(new Option(q,q)));
});
document.getElementById('download').addEventListener('click',async()=>{
  const url=encodeURIComponent(document.getElementById('url').value);
  const resl=encodeURIComponent(document.getElementById('qualities').value);
  const bar=document.getElementById('progressBar'),cont=document.getElementById('progressContainer'),stats=document.getElementById('stats'),sz=document.getElementById('stat-size'),spd=document.getElementById('stat-speed'),etaEl=document.getElementById('stat-eta');
  cont.classList.remove('d-none'); stats.classList.remove('d-none'); bar.style.width='0%';
  const response=await fetch(`/download?url=${url}&resolution=${resl}`);
  total=Number(response.headers.get('Content-Length'));
  start=Date.now()/1000;
  sz.textContent=`${(total/1048576).toFixed(2)} MB`;
  const reader=response.body.getReader(); let received=0;
  while(true){
    const {done,value}=await reader.read(); if(done) break;
    received+=value.length;
    const pct=(received/total*100).toFixed(1);
    const elapsed=Date.now()/1000-start;
    const speed=`${(received/1048576/elapsed).toFixed(2)} MB/s`;
    const etaVal=fmtTime(elapsed*(total/received-1));
    bar.style.width=`${pct}%`;
    spd.textContent=speed; etaEl.textContent=etaVal;
  }
  const blob=new Blob(await response.arrayBuffer());
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=document.getElementById('title').textContent+'.mp4';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  cont.classList.add('d-none'); stats.classList.add('d-none');
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
    parser=argparse.ArgumentParser(description="YouTube Downloader Web Server")
    parser.add_argument('--test',action='store_true')
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=5111)
    args=parser.parse_args()
    if args.test:
        unittest.main(argv=[sys.argv[0]])
    else:
        try:
            server=ThreadingHTTPServer((args.host,args.port),YouTubeHandler)
            print(f"Server on {args.host}:{args.port}")
        except OSError:
            print("Port busy, using ephemeral",file=sys.stderr)
            server=ThreadingHTTPServer((args.host,0),YouTubeHandler)
            print(f"Server on {args.host}:{server.server_address[1]}")
        server.serve_forever()

if __name__=='__main__': main()
