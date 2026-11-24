import os, time, threading, subprocess, pathlib, secrets, json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from html import escape
from hmac import compare_digest
import base64, re
ROOT=pathlib.Path(os.getenv('DATA_DIR','/tmp/chercha')); ROOT.mkdir(parents=True,exist_ok=True)
PASSWORD=os.getenv('APP_PASSWORD',''); LOCK=threading.Lock(); state={'status':'Listo','job':''}; worker=None

def run(source):
    job=ROOT/secrets.token_hex(8); job.mkdir(); state.update(status='Capturando',job=job.name)
    try:
        cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error']
        if source.startswith('https://www.youtube.com/watch?v='):
            state['status']='Conectando con YouTube'
            result=subprocess.run(['yt-dlp','--no-playlist','--no-warnings','-f','best[height<=480][protocol^=m3u8]/best[height<=480]','-g',source],capture_output=True,text=True,timeout=45)
            urls=result.stdout.strip().splitlines()
            if result.returncode or len(urls)!=1 or urlparse(urls[0]).scheme!='https':
                state['status']='YouTube no disponible desde el servidor: '+(result.stderr.strip().splitlines()[-1][:250] if result.stderr.strip() else 'sin formato compatible'); return
            source=urls[0]; state['status']='Capturando'
        if source=='demo': cmd+=['-re','-f','lavfi','-i','testsrc2=size=640x360:rate=15']
        else: cmd+=['-rw_timeout','15000000','-i',source]
        cmd+=['-t','120','-an','-vf','scale=360:640:force_original_aspect_ratio=increase,crop=360:640','-c:v','libx264','-preset','ultrafast','-threads','1','-r','15','-g','225','-force_key_frames','expr:gte(t,n_forced*15)','-f','segment','-segment_time','15','-reset_timestamps','1',str(job/'clip_%03d.mp4')]
        # Preserve audio for actual streams.
        if source!='demo': cmd.remove('-an'); cmd[cmd.index('-f',cmd.index('-g')):cmd.index('-f',cmd.index('-g'))]=['-c:a','aac']
        p=subprocess.run(cmd,capture_output=True,timeout=160)
        state['status']='Finalizado' if p.returncode==0 else 'Error de captura (comprueba la fuente)'
    except Exception: state['status']='Error o tiempo de espera agotado'

class Handler(BaseHTTPRequestHandler):
    def auth(self):
        expected='Basic '+base64.b64encode(('admin:'+PASSWORD).encode()).decode()
        if PASSWORD and compare_digest(self.headers.get('Authorization',''),expected): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Chercha"'); self.end_headers(); return False
    def do_GET(self):
        if not self.auth(): return
        path=urlparse(self.path).path
        if path.startswith('/clip/'):
            parts=path.split('/'); f=ROOT.joinpath(*parts[2:]);
            if len(parts)!=4 or not f.resolve().is_relative_to(ROOT.resolve()) or f.suffix!='.mp4' or not f.is_file(): self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type','video/mp4'); self.send_header('Content-Length',str(f.stat().st_size)); self.end_headers(); self.wfile.write(f.read_bytes()); return
        job=ROOT/state['job']; clips=sorted(job.glob('clip_*.mp4')) if state['job'] else []; clips=clips[:-1] if state['status']=='Capturando' else clips
        gallery=''.join(f'<article><video controls preload="none" src="/clip/{job.name}/{f.name}"></video><a download href="/clip/{job.name}/{f.name}">Descargar {f.name}</a></article>' for f in clips)
        page=f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>CHERCHA CLIP AI</title><style>body{{background:#111827;color:#eee;font:16px system-ui;max-width:900px;margin:40px auto;padding:20px}}input,button{{padding:12px;border-radius:8px;border:0;margin:8px}}input{{width:65%}}button{{background:#7257e8;color:white}}section{{display:flex;flex-wrap:wrap;gap:20px}}video{{width:160px;display:block}}a{{color:#aabaff}}</style><h1>CHERCHA CLIP AI</h1><p>Versión 0.2 · prueba YouTube y captura segmentada, sin IA todavía</p><form method="post"><input name="source" placeholder="Enlace YouTube autorizado, HLS o demo" required><button>Iniciar prueba</button></form><p>Estado: {escape(state['status'])}</p><a href="/">Actualizar galería</a><section>{gallery}</section>'''
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(page.encode())
    def do_POST(self):
        global worker
        if not self.auth(): return
        if self.headers.get('Origin') and urlparse(self.headers['Origin']).netloc!=self.headers.get('Host'): self.send_error(403); return
        n=int(self.headers.get('Content-Length',0));
        if n>4096: self.send_error(413); return
        source=parse_qs(self.rfile.read(n).decode()).get('source',[''])[0].strip()
        # Only operator-configured source; avoids arbitrary server-side requests.
        if source!='demo' and not re.fullmatch(r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}',source) and (not os.getenv('AUTHORIZED_HLS_URL') or source!=os.getenv('AUTHORIZED_HLS_URL')): self.send_error(400,'Fuente no autorizada'); return
        with LOCK:
            if worker and worker.is_alive(): self.send_error(409,'Prueba en curso'); return
            worker=threading.Thread(target=run,args=(source,),daemon=True); worker.start()
        self.send_response(303); self.send_header('Location','/'); self.end_headers()
if __name__=='__main__':
    if not PASSWORD: raise SystemExit('Configura APP_PASSWORD')
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Handler).serve_forever()
