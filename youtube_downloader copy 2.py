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

# Dependency check: try yt_dlp or youtube_dl, otherwise exit with instruction
try:
    from yt_dlp import YoutubeDL
    using_module = 'yt_dlp'
except ImportError:
    try:
        from youtube_dl import YoutubeDL
        using_module = 'youtube_dl'
    except ImportError:
        sys.stderr.write(
            'Error: Neither yt_dlp nor youtube_dl is installed.\n'
            'Install one of them via `pip install yt-dlp` or `pip install youtube_dl`\n'
        )
        sys.exit(1)

# --- Core Functions ---
def parse_video_id(url: str) -> str:
    """
    Extracts an 11-character YouTube video ID from various URL formats.
    """
    patterns = [
        r"v=([0-9A-Za-z_-]{11})",          # https://www.youtube.com/watch?v=VIDEOID
        r"youtu\.be/([0-9A-Za-z_-]{11})", # https://youtu.be/VIDEOID
        r"embed/([0-9A-Za-z_-]{11})"       # https://www.youtube.com/embed/VIDEOID
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid YouTube URL: {url}")


def get_video_info(url: str) -> dict:
    """
    Returns video title, thumbnail URL, and available resolutions.
    """
    ydl_opts = {'quiet': True, 'skip_download': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    title = info.get('title')
    thumbnail = info.get('thumbnail') or ''
    qualities = []
    for fmt in info.get('formats', []):
        height = fmt.get('height')
        if height:
            tag = f"{height}p"
            if tag not in qualities:
                qualities.append(tag)
    return {'title': title, 'thumbnail_url': thumbnail, 'qualities': qualities, 'module': using_module}


def download_and_merge(url: str, resolution: str) -> str:
    """
    Download and merge video+audio at the specified resolution. Returns filepath.
    """
    height = int(resolution.rstrip('p'))
    fmt_select = f"bestvideo[height<={height}]+bestaudio/best"
    tmpdir = tempfile.mkdtemp(prefix='ytdl_')
    outtmpl = os.path.join(tmpdir, '%(id)s.%(ext)s')
    ydl_opts = {
        'quiet': True,
        'format': fmt_select,
        'outtmpl': outtmpl,
        'merge_output_format': 'mp4'
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vid = info.get('id')
    filepath = os.path.join(tmpdir, f"{vid}.mp4")
    if not os.path.exists(filepath):
        files = os.listdir(tmpdir)
        if files:
            filepath = os.path.join(tmpdir, files[0])
    return filepath

# --- HTTP Handler ---
class YouTubeHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200, content_type='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        if self.path != '/fetch':
            self.send_error(404)
            return
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
                fp = download_and_merge(unquote(url), unquote(res))
                size = os.path.getsize(fp)
                name = os.path.basename(fp)
                extra = {
                    'Content-Length': str(size),
                    'Content-Disposition': f'attachment; filename="{name}"'
                }
                self._set_headers(200, 'video/mp4', extra)
                with open(fp, 'rb') as f:
                    while True:
                        chunk = f.read(1024*512)
                        if not chunk: break
                        self.wfile.write(chunk)
                os.remove(fp)
                os.rmdir(os.path.dirname(fp))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
        self.send_error(404)

# --- HTML Template ---
INDEX_HTML = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>YouTube Downloader</title>
<style>body{font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;}img{max-width:100%;height:auto;}progress{width:100%;height:20px;}.hidden{display:none;}#stats{margin-top:10px;}</style>
</head><body>
<h1>YouTube Downloader</h1>
<input id=\"url\" type=\"text\" placeholder=\"Enter YouTube URL\" style=\"width:80%;\" />
<button id=\"fetch\">Fetch Info</button>
<div id=\"info\" class=\"hidden\"><h2 id=\"title\"></h2><p id=\"module\"></p><img id=\"thumbnail\" src=\"\" alt=\"Thumbnail\" /><div><label for=\"qualities\">Quality:</label><select id=\"qualities\"></select><button id=\"download\">Download</button></div><br><progress id=\"progressBar\" value=\"0\" max=\"100\" class=\"hidden\"></progress><div id=\"stats\" class=\"hidden\"></div></div>
<script>
function fmtTime(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60),secs=Math.floor(s%60);return[h,m,secs].map(x=>x.toString().padStart(2,'0')).join(':');}
let total=0,start=0;
const fetchBtn=document.getElementById('fetch');fetchBtn.onclick=()=>{fetch('/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:document.getElementById('url').value})}).then(r=>r.json()).then(d=>{if(d.error){alert(d.error);return;}document.getElementById('info').classList.remove('hidden');document.getElementById('title').textContent=d.title;document.getElementById('module').textContent=`Using: ${d.module}`;document.getElementById('thumbnail').src=d.thumbnail_url;let sel=document.getElementById('qualities');sel.innerHTML='';d.qualities.forEach(q=>sel.append(new Option(q,q)));});};
const downBtn=document.getElementById('download');downBtn.onclick=()=>{const u=encodeURIComponent(document.getElementById('url').value),r=encodeURIComponent(document.getElementById('qualities').value),pb=document.getElementById('progressBar'),st=document.getElementById('stats');pb.classList.remove('hidden');st.classList.remove('hidden');const xhr=new XMLHttpRequest();xhr.open('GET',`/download?url=${u}&resolution=${r}`);xhr.responseType='blob';xhr.onreadystatechange=()=>{if(xhr.readyState===XHR.HEADERS_RECEIVED){total=parseInt(xhr.getResponseHeader('Content-Length'));st.textContent=`Size: ${(total/1048576).toFixed(2)} MB`;start=Date.now()/1000;}};xhr.onprogress=e=>{if(total){const l=e.loaded,e_sec=Date.now()/1000-start,mb=l/1048576,to=total/1048576,perc=(l/total*100).toFixed(1),eta=fmtTime(e_sec*(total/l-1));st.textContent=`${mb.toFixed(2)}/${to.toFixed(2)} MB (${perc}%) ETA ${eta}`;pb.value=l/total*100;}};xhr.onload=()=>{const b=xhr.response,a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=document.getElementById('title').textContent+'.mp4';document.body.appendChild(a);a.click();document.body.removeChild(a);pb.classList.add('hidden');st.classList.add('hidden');};xhr.onerror=()=>{alert('Download failed');pb.classList.add('hidden');st.classList.add('hidden');};xhr.send();};
</script>
</body></html>"""

# --- Tests ---
class TestParseVideoID(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_short(self):
        self.assertEqual(parse_video_id("https://youtu.be/dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_embed(self):
        self.assertEqual(parse_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),"dQw4w9WgXcQ")
    def test_invalid(self):
        with self.assertRaises(ValueError): parse_video_id("not a url")

# --- Entry Point ---
def main():
    parser=argparse.ArgumentParser(description="YouTube Downloader Web Server")
    parser.add_argument('--test',action='store_true',help='Run tests')
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=5000)
    args=parser.parse_args()
    if args.test:
        unittest.main(argv=[sys.argv[0]])
    else:
        host,port=args.host,args.port
        try:
            srv=ThreadingHTTPServer((host,port),YouTubeHandler)
            print(f"Server on {host}:{port}")
        except OSError:
            print(f"Port busy, using ephemeral",file=sys.stderr)
            srv=ThreadingHTTPServer((host,0),YouTubeHandler)
            print(f"Server on {host}:{srv.server_address[1]}")
        srv.serve_forever()

if __name__=='__main__': main()
