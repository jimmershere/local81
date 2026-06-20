"""Render a *runnable*, functional-out-of-the-box control surface from a catalog.

This is the heaviest renderer. It emits a self-contained directory you can run
two ways:

* **Push-button (click-first):** ``python3 control_server.py`` serves the
  accessible ``launcher.html`` and, on a button press, runs the matching
  dispatcher script (``local81 ...``) on the control node. No Semaphore, no n8n,
  no typing — pick a category, an action, a host group, press Run. This is the
  surface built for operators who would rather click than type.
* **Multi-user (enterprise):** ``docker compose up`` brings up Semaphore (MIT) +
  PostgreSQL 17 for RBAC/LDAP/OIDC, fronting the same CLI.

Plus the catalog's roles as a buildable fleet, so there's something real to
deploy to. Still pure: returns ``{relative_path: text}``, writes nothing, and
never bakes a secret literal.
"""

from __future__ import annotations

import json

from ..recipes import Catalog
from .fleet_build import role_dockerfile
from .semaphore import render_catalog_scripts


def render_stack(
    catalog: Catalog,
    *,
    db_password_ref: str,
    image: str = "semaphoreui/semaphore:latest",
    pg_image: str = "postgres:17",
    semaphore_port: int = 3000,
    ui_port: int = 8080,
) -> dict[str, str]:
    """Return the file tree (path -> contents) for the runnable control surface."""
    files: dict[str, str] = {}

    # The click-first control server + accessible launcher (the default surface).
    files["control_server.py"] = _control_server()
    files["launcher.html"] = _LAUNCHER_HTML.replace("__CATALOG_NAME__", catalog.name)
    files["actions.json"] = json.dumps(_actions(catalog, semaphore_port), indent=2)

    # The executable backbone the server (and Semaphore/n8n) run.
    dispatchers, build_scripts = render_catalog_scripts(catalog)
    files.update(dispatchers)
    files.update(build_scripts)

    # The enterprise Semaphore option.
    files["docker-compose.semaphore.yml"] = _semaphore_compose(image, pg_image, semaphore_port)
    files[".env.example"] = _env_example(db_password_ref)

    # The fleet to deploy to.
    files["fleet/Dockerfile.role"] = role_dockerfile(catalog)
    files["fleet/docker-compose.fleet.yml"] = _fleet_compose(catalog)

    # One-command bring-up + docs.
    files["start.sh"] = _start_script(catalog, ui_port)
    files["README.md"] = _stack_readme(catalog, semaphore_port, ui_port)
    return files


# --- the whitelist the control server validates against --------------------


def _actions(catalog: Catalog, semaphore_port: int) -> dict:
    cats = []
    for cat in catalog.categories:
        used: list[str] = []
        for o in cat.options:
            for name in o.placeholders():
                if name not in used:
                    used.append(name)
        params = []
        for name in used:
            p = catalog.parameters[name]
            params.append({"key": p.key, "type": p.type, "default": str(p.default),
                           "choices": list(p.choices)})
        cats.append({
            "key": cat.key,
            "title": cat.title,
            "scoped": cat.key == "deploy",
            "options": [{"key": o.key, "label": o.label, "mutating": o.mutating} for o in cat.options],
            "params": params,
            "groups": [{"key": g.key, "label": g.label, "count": len(g.members)} for g in catalog.groups],
        })
    return {"catalog": catalog.name, "semaphore_url": f"http://localhost:{semaphore_port}",
            "categories": cats}


# --- the control server (stdlib only) --------------------------------------


def _control_server() -> str:
    return r'''#!/usr/bin/env python3
"""Local-81 click-first control server (stdlib only).

Serves launcher.html and, on POST /run, executes the matching dispatcher script
on the control node. Defense in depth: it binds 127.0.0.1 by default AND /run
requires a shared token (the X-Local81-Token header). The token is taken from
LOCAL81_UI_TOKEN or generated at startup and printed once; the launcher picks it
up from the ?t=... in the URL the server prints. Mutating actions additionally
require an explicit {"confirm": true}. Every (category, action) is validated
against actions.json before anything runs, and only the pre-generated dispatch
script is invoked (never an arbitrary command), so the web surface cannot widen
what the CLI can already do.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTIONS = json.loads((HERE / "actions.json").read_text(encoding="utf-8"))
BY_KEY = {c["key"]: c for c in ACTIONS["categories"]}
TOKEN = os.environ.get("LOCAL81_UI_TOKEN") or secrets.token_urlsafe(24)
# Where `local81` runs (must hold the .local81/ project). Defaults to the dir the
# scripts live in; point it at your project root with --project-dir / LOCAL81_DIR.
PROJECT_DIR = HERE


def _lookup(category: str, action: str):
    cat = BY_KEY.get(category)
    if not cat:
        return None, None
    opt = next((o for o in cat["options"] if o["key"] == action), None)
    return cat, opt


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "launcher.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/actions.json":
            return self._send(200, json.dumps(ACTIONS), "application/json")
        if self.path.startswith("/assets/"):
            return self._serve_asset(self.path[len("/assets/"):])
        return self._send(404, json.dumps({"error": "not found"}))

    def _serve_asset(self, name):
        # Path-safe: basename only, must live under HERE/assets, no traversal.
        safe = os.path.basename(name)
        path = (HERE / "assets" / safe).resolve()
        if not str(path).startswith(str((HERE / "assets").resolve())) or not path.is_file():
            return self._send(404, json.dumps({"error": "asset not found"}))
        ctypes = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon"}
        ctype = ctypes.get(path.suffix.lower(), "application/octet-stream")
        return self._send(200, path.read_bytes(), ctype)

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, json.dumps({"error": "not found"}))
        if not secrets.compare_digest(self.headers.get("X-Local81-Token", ""), TOKEN):
            return self._send(401, json.dumps({"error": "missing or invalid token "
                                               "(open the URL the server printed, it carries ?t=...)"}))
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._send(400, json.dumps({"error": "invalid JSON"}))

        category = str(req.get("category", ""))
        action = str(req.get("action", ""))
        hosts = str(req.get("hosts", "all"))
        params = req.get("params", {}) or {}
        cat, opt = _lookup(category, action)
        if not cat or not opt:
            return self._send(400, json.dumps({"error": f"unknown action {category}/{action}"}))
        if opt["mutating"] and not req.get("confirm"):
            return self._send(409, json.dumps({"error": "confirmation required", "mutating": True}))

        script = HERE / "dispatch" / f"{category}.sh"
        if not script.is_file():
            return self._send(500, json.dumps({"error": f"missing dispatcher {script.name}"}))

        env = {"action": action, "hosts": hosts}
        allowed = {p["key"] for p in cat["params"]}
        for k, v in params.items():
            if k in allowed:
                env[str(k)] = str(v)
        run_env = {**os.environ, **env}
        try:
            proc = subprocess.run(["bash", str(script)], cwd=str(PROJECT_DIR), env=run_env,
                                  capture_output=True, text=True, timeout=900)
            out = (proc.stdout or "") + (proc.stderr or "")
            return self._send(200, json.dumps({"exitCode": proc.returncode, "output": out}))
        except subprocess.TimeoutExpired:
            return self._send(504, json.dumps({"error": "timed out after 900s"}))
        except Exception as exc:  # surface, do not crash the server
            return self._send(500, json.dumps({"error": str(exc)}))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Local-81 click-first control server")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default localhost only)")
    ap.add_argument("--port", type=int, default=8181)
    ap.add_argument("--project-dir", default=os.environ.get("LOCAL81_DIR"),
                    help="project root that holds .local81/ (where local81 runs). Default: this dir.")
    args = ap.parse_args()
    global PROJECT_DIR
    if args.project_dir:
        PROJECT_DIR = Path(args.project_dir).resolve()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Local-81 control panel (catalog: {ACTIONS['catalog']}, project: {PROJECT_DIR})")
    print(f"  open: http://{args.host}:{args.port}/?t={TOKEN}")
    print("  (the ?t=... token is required to run actions; keep this URL private)")
    print("Press Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
'''


# --- the accessible, click-first launcher ----------------------------------
# Static HTML/JS; it fetches /actions.json and builds the flow generically, so
# there is one file regardless of catalog. Design goals: every control is a real
# <button> with a big hit area, full keyboard operation, visible focus, high
# contrast, no hover-only affordances, motion gated on prefers-reduced-motion,
# and an aria-live results region. Typing is avoided — actions, host groups, and
# numeric parameters are all chosen by clicking.

_LAUNCHER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__CATALOG_NAME__ — Local-81 control panel</title>
<style>
  :root{
    --bg:#0a0e17; --panel:#11161f; --panel2:#161d29; --ink:#eef1f5; --mut:#8a93a3;
    --line:#222b39; --gold:#d6a23a; --gold-ink:#1a1206; --acc:#58a6ff; --ok:#3fb950;
    --warn:#e3b341; --danger:#e5533d; --tap:72px; --radius:14px;
  }
  *{ box-sizing:border-box; }
  html{ font-size:18px; }
  body{ margin:0; background:var(--bg); color:var(--ink);
        font:1rem/1.6 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  .skip{ position:absolute; left:-999px; top:0; background:var(--gold); color:var(--gold-ink); padding:12px 18px; z-index:10; font-weight:600; }
  .skip:focus{ left:8px; top:8px; }
  header{ max-width:940px; margin:0 auto; padding:22px 20px 10px; }
  .brand{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  .seal{ height:76px; width:auto; flex:none; border:1px solid var(--line); border-radius:12px; background:#000; }
  .brand-words{ display:flex; flex-direction:column; gap:2px; }
  .wordmark{ font-size:1.5rem; font-weight:700; letter-spacing:.04em; }
  .wordmark .cp{ color:var(--gold); font-weight:500; letter-spacing:.02em; }
  .tag{ color:var(--mut); font-size:.95rem; }
  .status{ margin-left:auto; display:flex; align-items:center; gap:8px; color:var(--mut);
           font-size:.85rem; border:1px solid var(--line); border-radius:999px; padding:6px 14px; }
  .dot-live{ width:9px; height:9px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 0 rgba(63,185,80,.6); }
  @media (prefers-reduced-motion: no-preference){ .dot-live{ animation:pulse 2.4s infinite; } }
  @keyframes pulse{ 0%{box-shadow:0 0 0 0 rgba(63,185,80,.5)} 70%{box-shadow:0 0 0 7px rgba(63,185,80,0)} 100%{box-shadow:0 0 0 0 rgba(63,185,80,0)} }
  .hero{ max-width:940px; margin:10px auto 0; padding:0 20px; }
  .hero img{ width:100%; max-height:170px; object-fit:cover; object-position:center 30%;
             border:1px solid var(--line); border-radius:var(--radius); display:block; background:#000; }
  main{ max-width:940px; margin:0 auto; padding:8px 20px 48px; }
  .about{ margin:16px 0 4px; border:1px solid var(--line); border-radius:var(--radius); background:var(--panel); }
  .about summary{ cursor:pointer; padding:14px 18px; color:var(--gold); font-weight:500; list-style:none; }
  .about summary::-webkit-details-marker{ display:none; }
  .about summary::before{ content:"›"; display:inline-block; margin-right:10px; transition:transform .15s; }
  .about[open] summary::before{ transform:rotate(90deg); }
  .about p{ margin:0; padding:0 18px 16px; color:var(--mut); }
  .about code{ color:var(--ink); background:var(--panel2); padding:1px 6px; border-radius:6px; }
  .crumbs{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:18px 0; color:var(--mut); }
  .crumbs b{ color:var(--ink); font-weight:500; }
  .lead{ font-size:.95rem; color:var(--mut); margin:0 0 4px; }
  .grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(248px,1fr)); gap:14px; }
  button{ font:inherit; color:var(--ink); background:var(--panel); border:2px solid var(--line);
          border-radius:var(--radius); padding:18px; min-height:var(--tap); text-align:left;
          cursor:pointer; width:100%; display:flex; align-items:center; gap:14px; }
  button:hover{ border-color:var(--gold); }
  button:focus-visible{ outline:4px solid var(--acc); outline-offset:2px; }
  button[aria-pressed="true"]{ border-color:var(--gold); background:var(--panel2); }
  .big{ font-size:1.12rem; font-weight:500; }
  .icon{ width:42px; height:42px; min-width:42px; border-radius:11px; background:var(--panel2);
         border:1px solid var(--line); color:var(--gold); display:grid; place-items:center; }
  .icon svg{ width:24px; height:24px; }
  .sub{ display:block; color:var(--mut); font-size:.9rem; font-weight:400; margin-top:2px; }
  .mut-tag{ margin-left:auto; color:var(--warn); font-size:.82rem; border:1px solid var(--warn); border-radius:999px; padding:3px 11px; white-space:nowrap; }
  .section-h{ font-size:.78rem; text-transform:uppercase; letter-spacing:.13em; color:var(--gold);
              margin:24px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
  .stepper{ display:flex; align-items:center; gap:0; }
  .stepper button{ width:var(--tap); min-width:var(--tap); justify-content:center; font-size:1.5rem; }
  .stepper output{ min-width:80px; text-align:center; font-size:1.3rem; font-weight:600; padding:0 10px; }
  .row{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
  .field{ display:flex; flex-direction:column; gap:6px; margin:10px 0; }
  .field label{ color:var(--mut); }
  .field input{ font:inherit; min-height:var(--tap); padding:0 16px; border-radius:var(--radius);
                border:2px solid var(--line); background:var(--panel); color:var(--ink); }
  .runbar{ position:sticky; bottom:0; background:linear-gradient(180deg,transparent,var(--bg) 32%);
           padding:16px 0 8px; margin-top:20px; }
  .run{ background:var(--gold); color:var(--gold-ink); border-color:var(--gold); justify-content:center; font-size:1.18rem; font-weight:700; }
  .run.danger{ background:var(--danger); border-color:var(--danger); color:#fff; }
  .run:disabled{ opacity:.45; cursor:not-allowed; }
  .ghost{ background:transparent; }
  .results{ margin-top:18px; background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); overflow:hidden; }
  .results h2{ font-size:.78rem; text-transform:uppercase; letter-spacing:.13em; color:var(--mut); margin:0;
               padding:14px 16px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:10px; }
  .results pre{ margin:0; padding:16px; overflow:auto; max-height:360px; font:0.92rem/1.5 ui-monospace,Menlo,Consolas,monospace; white-space:pre-wrap; }
  .badge{ font-size:.8rem; padding:3px 11px; border-radius:999px; }
  .badge.ok{ background:rgba(63,185,80,.16); color:var(--ok); }
  .badge.err{ background:rgba(229,83,61,.18); color:var(--danger); }
  footer{ max-width:940px; margin:0 auto; padding:8px 20px 40px; color:var(--mut); font-size:.85rem;
          border-top:1px solid var(--line); display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  footer .sep{ color:var(--line); }
  footer a{ color:var(--gold); }
  .modal{ position:fixed; inset:0; background:rgba(5,8,13,.7); display:grid; place-items:center; padding:20px; }
  .modal[hidden]{ display:none; }
  .modal .card{ background:var(--panel); border:2px solid var(--danger); border-radius:var(--radius); padding:24px; max-width:460px; width:100%; }
  .modal h2{ margin:0 0 8px; color:var(--danger); }
  .modal .row{ margin-top:18px; }
  @media (prefers-reduced-motion: no-preference){ button,.run{ transition:border-color .12s, background .12s; } }
  .sr-only{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0; }
</style>
</head>
<body>
<a class="skip" href="#main">Skip to controls</a>
<header>
  <div class="brand">
    <img class="seal" src="assets/local81-emblem.png" alt="Local-81 — Clem the operator, Fully Baked Orchestration, Traverse City Michigan" onerror="this.style.display='none'">
    <div class="brand-words">
      <div class="wordmark">LOCAL&#8209;81 <span class="cp">control panel</span></div>
      <div class="tag">Fully baked orchestration &middot; operators hold the line</div>
    </div>
    <div class="status"><span class="dot-live" aria-hidden="true"></span> <span id="status-text">control plane online</span></div>
  </div>
</header>
<div class="hero">
  <img src="assets/local81-emblem.png" alt="Local-81 — Clem the operator on a Traverse City beachhead" onerror="this.closest('.hero').style.display='none'">
</div>
<main id="main">
  <details class="about">
    <summary>What am I looking at?</summary>
    <p><b>Local-81</b> is a push-based deploy &amp; runbook control plane over plain SSH + rsync — no agent on your hosts. Pick a task, choose where it runs, press the big button. Every button runs the same <code>local81</code> command an operator would type by hand, then records a tamper-evident receipt. Nothing here holds your servers' SSH keys.</p>
  </details>
  <p class="lead">Pick a task, choose where it runs, press the button.</p>
  <nav class="crumbs" id="crumbs" aria-label="Where you are"></nav>
  <div id="screen" role="region" aria-live="polite"></div>
  <div class="runbar" id="runbar" hidden></div>
  <section class="results" id="results" hidden>
    <h2>Result <span id="result-badge"></span></h2>
    <pre id="result-out" tabindex="0" aria-live="polite"></pre>
  </section>
</main>
<footer>
  <span>Built in Port, Proven at Sea</span> <span class="sep">&middot;</span>
  <span>Powered by <a href="https://portwright.io">Portwright.io</a></span> <span class="sep">&middot;</span>
  <span>generated from <code style="color:var(--ink)">__CATALOG_NAME__.yaml</code></span>
</footer>

<div class="modal" id="confirm" hidden role="dialog" aria-modal="true" aria-labelledby="confirm-h">
  <div class="card">
    <h2 id="confirm-h">Confirm — this changes hosts</h2>
    <p id="confirm-body" class="tag"></p>
    <div class="row">
      <button class="run danger" id="confirm-yes" style="flex:1">Yes, run it</button>
      <button class="ghost" id="confirm-no" style="flex:1; justify-content:center">Cancel</button>
    </div>
  </div>
</div>

<script>
// Token: take it from ?t=... if present and remember it, so a refresh or a bare
// URL still works. Falls back to a one-time paste if it was never provided.
const qsToken = new URLSearchParams(location.search).get('t');
function saveTok(t){ try{ localStorage.setItem('l81_token', t); }catch(e){} }
function readTok(){ try{ return localStorage.getItem('l81_token')||''; }catch(e){ return ''; } }
if(qsToken){ saveTok(qsToken); }
let TOKEN = qsToken || readTok();
const state = { cats:[], cat:null, action:null, hosts:null, params:{}, semaphore:"" };
const el = (t,p={})=>Object.assign(document.createElement(t),p);

const SVG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">';
const ICONS={
  deploy: SVG+'<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/><path d="M5 21h14"/></svg>',
  observe: SVG+'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
  audit: SVG+'<path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></svg>',
  govern: SVG+'<path d="M3 21h18"/><path d="M5 21V10"/><path d="M19 21V10"/><path d="M9 21V10"/><path d="M15 21V10"/><path d="m12 3 8 5H4l8-5Z"/></svg>',
  ops: SVG+'<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></svg>'
};
const ICON_FALLBACK = SVG+'<path d="m7 8 4 4-4 4"/><path d="M13 16h4"/></svg>';

function tokenBanner(msg){
  let b=document.getElementById('tokbar');
  if(!b){ b=el('div',{id:'tokbar'}); b.style.cssText='background:var(--panel2);border:2px solid var(--warn);border-radius:var(--radius);padding:14px;margin:0 0 16px;'; document.getElementById('main').prepend(b); }
  b.innerHTML='';
  b.append(el('p',{textContent: msg || 'Enter your access token (it is in the URL the server printed, after ?t=).', style:'margin:0 0 10px'}));
  const row=el('div',{className:'row'});
  const inp=el('input',{type:'text', value:TOKEN}); inp.setAttribute('aria-label','access token');
  inp.style.cssText='flex:1;min-height:var(--tap);padding:0 14px;border-radius:var(--radius);border:2px solid var(--line);background:var(--panel);color:var(--ink);font:inherit;';
  const save=el('button',{textContent:'Save token'}); save.style.cssText='width:auto;';
  save.onclick=()=>{ TOKEN=inp.value.trim(); saveTok(TOKEN); b.remove(); };
  inp.addEventListener('keydown',e=>{ if(e.key==='Enter') save.onclick(); });
  row.append(inp,save); b.append(row);
}
const screen = document.getElementById('screen');
const runbar = document.getElementById('runbar');

fetch('actions.json').then(r=>r.json()).then(d=>{
  state.cats = d.categories; state.semaphore = d.semaphore_url;
  if(d.catalog){ document.getElementById('status-text').textContent = 'control plane online · '+d.catalog; }
  showCategories();
  if(!TOKEN) tokenBanner();
}).catch(()=>{ screen.innerHTML='<p>Could not load actions.json. Is the control server running?</p>'; });

function crumbs(){
  const c = document.getElementById('crumbs'); c.innerHTML='';
  const home = el('button',{textContent:'All tasks', className:'ghost'});
  home.style.width='auto'; home.style.minHeight='44px'; home.style.padding='8px 14px';
  home.onclick=showCategories; c.appendChild(home);
  if(state.cat){ c.appendChild(el('span',{textContent:'›'})); c.appendChild(el('b',{textContent:state.cat.title})); }
  if(state.action){ c.appendChild(el('span',{textContent:'›'})); c.appendChild(el('b',{textContent:state.action.label})); }
}

function showCategories(){
  state.cat=state.action=state.hosts=null; state.params={};
  runbar.hidden=true; crumbs();
  screen.innerHTML='';
  const grid=el('div',{className:'grid'});
  state.cats.forEach(cat=>{
    const b=el('button',{className:'big'});
    const ic=el('span',{className:'icon'}); ic.innerHTML=ICONS[cat.key]||ICON_FALLBACK; b.append(ic);
    const wrap=el('span'); wrap.append(el('span',{textContent:cat.title}));
    wrap.append(el('span',{className:'sub',textContent:cat.options.length+' actions'}));
    b.append(wrap);
    b.onclick=()=>selectCategory(cat);
    grid.append(b);
  });
  screen.append(grid);
}

function selectCategory(cat){
  state.cat=cat; state.action=null; state.hosts = cat.scoped? null : 'all'; state.params={};
  cat.params.forEach(p=>state.params[p.key]=p.default);
  crumbs(); screen.innerHTML='';
  screen.append(el('h2',{className:'section-h',textContent:'Choose an action'}));
  const grid=el('div',{className:'grid'});
  cat.options.forEach(opt=>{
    const b=el('button',{className:'big'});
    b.append(el('span',{textContent:opt.label}));
    if(opt.mutating) b.append(el('span',{className:'mut-tag',textContent:'changes hosts'}));
    b.onclick=()=>selectAction(opt);
    grid.append(b);
  });
  screen.append(grid);
}

function selectAction(opt){
  state.action=opt; crumbs(); screen.innerHTML='';
  if(state.cat.scoped){
    screen.append(el('h2',{className:'section-h',textContent:'Which hosts'}));
    const grid=el('div',{className:'grid'});
    state.cat.groups.forEach(g=>{
      const b=el('button',{className:'big'});
      b.setAttribute('aria-pressed', String(state.hosts===g.key));
      b.append(el('span',{textContent:g.label}));
      b.append(el('span',{className:'sub',textContent:g.count+' host'+(g.count>1?'s':'')}));
      b.onclick=()=>{ state.hosts=g.key; selectAction(opt); };
      grid.append(b);
    });
    screen.append(grid);
  }
  renderParams();
  renderRunbar();
}

function renderParams(){
  const ps=state.cat.params; if(!ps.length) return;
  screen.append(el('h2',{className:'section-h',textContent:'Options'}));
  ps.forEach(p=>{
    if(p.type==='int'){
      const f=el('div',{className:'field'});
      f.append(el('label',{textContent:p.key, id:'lbl-'+p.key}));
      const s=el('div',{className:'stepper'});
      const minus=el('button',{textContent:'−','aria-label':'decrease '+p.key});
      const out=el('output',{textContent:state.params[p.key]}); out.setAttribute('aria-labelledby','lbl-'+p.key);
      const plus=el('button',{textContent:'+','aria-label':'increase '+p.key});
      minus.onclick=()=>{ state.params[p.key]=Math.max(0,(+state.params[p.key]||0)-1); out.textContent=state.params[p.key]; };
      plus.onclick=()=>{ state.params[p.key]=(+state.params[p.key]||0)+1; out.textContent=state.params[p.key]; };
      s.append(minus,out,plus); f.append(s); screen.append(f);
    } else if(p.type==='enum'){
      const f=el('div',{className:'field'});
      f.append(el('label',{textContent:p.key}));
      const row=el('div',{className:'row'});
      p.choices.forEach(c=>{
        const b=el('button'); b.style.width='auto'; b.textContent=c;
        b.setAttribute('aria-pressed', String(state.params[p.key]===c));
        b.onclick=()=>{ state.params[p.key]=c; renderRefresh(); };
        row.append(b);
      });
      f.append(row); screen.append(f);
    } else {
      const f=el('div',{className:'field'});
      f.append(el('label',{textContent:p.key, htmlFor:'in-'+p.key}));
      const i=el('input',{id:'in-'+p.key, value:state.params[p.key]||''});
      i.oninput=()=>{ state.params[p.key]=i.value; };
      f.append(i); screen.append(f);
    }
  });
}

function renderRefresh(){ selectAction(state.action); }

function summary(){
  let s = state.action.label;
  if(state.cat.scoped) s += ' · '+(state.cat.groups.find(g=>g.key===state.hosts)?.label||'');
  return s;
}

function renderRunbar(){
  runbar.hidden=false; runbar.innerHTML='';
  const ready = !state.cat.scoped || state.hosts;
  const b=el('button',{className:'run big'+(state.action.mutating?' danger':'')});
  b.textContent = ready ? ('Run — '+summary()) : 'Pick a host group first';
  b.disabled = !ready;
  b.onclick = ()=> state.action.mutating ? askConfirm() : run();
  runbar.append(b);
}

function askConfirm(){
  const m=document.getElementById('confirm');
  document.getElementById('confirm-body').textContent = 'About to '+summary()+'. This changes hosts.';
  m.hidden=false; document.getElementById('confirm-yes').focus();
  document.getElementById('confirm-yes').onclick=()=>{ m.hidden=true; run(); };
  document.getElementById('confirm-no').onclick=()=>{ m.hidden=true; };
}

function run(){
  const res=document.getElementById('results'); const out=document.getElementById('result-out');
  const badge=document.getElementById('result-badge');
  res.hidden=false; badge.textContent=''; out.textContent='Running '+summary()+' …';
  res.scrollIntoView({behavior:'smooth', block:'nearest'});
  fetch('run',{method:'POST',headers:{'Content-Type':'application/json','X-Local81-Token':TOKEN},
    body:JSON.stringify({category:state.cat.key, action:state.action.key, hosts:state.hosts||'all',
                         params:state.params, confirm:true})})
    .then(r=>{
      if(r.status===401){ badge.className='badge err'; badge.textContent='401';
        out.textContent='Access token missing or invalid. Enter it above, then press Run again.';
        tokenBanner('Access token missing or invalid — paste the current one (from the URL after ?t=).');
        return null; }
      return r.json();
    }).then(d=>{
      if(!d) return;
      if(d.error){ badge.className='badge err'; badge.textContent='error'; out.textContent=d.error; return; }
      const ok = d.exitCode===0;
      badge.className='badge '+(ok?'ok':'err'); badge.textContent = ok?'exit 0':('exit '+d.exitCode);
      out.textContent = d.output || '(no output)';
    }).catch(e=>{ badge.className='badge err'; badge.textContent='error'; out.textContent=String(e); });
}
</script>
</body>
</html>
"""


# --- enterprise Semaphore compose + env ------------------------------------


def _semaphore_compose(image: str, pg_image: str, port: int) -> str:
    return f"""# Enterprise option — Semaphore (MIT) + PostgreSQL 17, multi-user RBAC.
# `docker compose -f docker-compose.semaphore.yml --env-file .env up -d`
# (For the click-first push-button UI you don't need this — just run
#  `python3 control_server.py`.) No secret literals here; fill .env first.
name: local81-semaphore

services:
  postgres:
    image: {pg_image}
    environment:
      POSTGRES_DB: semaphore
      POSTGRES_USER: semaphore
      POSTGRES_PASSWORD: ${{SEMAPHORE_DB_PASSWORD:?set SEMAPHORE_DB_PASSWORD in .env}}
    volumes:
      - semaphore-pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U semaphore"]
      interval: 5s
      timeout: 5s
      retries: 10

  semaphore:
    image: {image}
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "{port}:3000"
    environment:
      SEMAPHORE_DB_DIALECT: postgres
      SEMAPHORE_DB_HOST: postgres
      SEMAPHORE_DB_PORT: "5432"
      SEMAPHORE_DB: semaphore
      SEMAPHORE_DB_USER: semaphore
      SEMAPHORE_DB_PASS: ${{SEMAPHORE_DB_PASSWORD:?set SEMAPHORE_DB_PASSWORD in .env}}
      SEMAPHORE_ADMIN: ${{SEMAPHORE_ADMIN:-admin}}
      SEMAPHORE_ADMIN_NAME: ${{SEMAPHORE_ADMIN:-admin}}
      SEMAPHORE_ADMIN_EMAIL: ${{SEMAPHORE_ADMIN_EMAIL:-admin@example.com}}
      SEMAPHORE_ADMIN_PASSWORD: ${{SEMAPHORE_ADMIN_PASSWORD:?set SEMAPHORE_ADMIN_PASSWORD in .env}}
      SEMAPHORE_ACCESS_KEY_ENCRYPTION: ${{SEMAPHORE_ACCESS_KEY_ENCRYPTION:?set SEMAPHORE_ACCESS_KEY_ENCRYPTION in .env}}
    volumes:
      - ${{LOCAL81_DIR:-../..}}:/srv/local81:rw
    restart: unless-stopped

volumes:
  semaphore-pg:
"""


def _env_example(db_password_ref: str) -> str:
    return f"""# Copy to .env and fill from your secret references — NEVER commit real values.
# Source of truth for the DB password reference: {db_password_ref}
#   openssl rand -base64 32   # good for SEMAPHORE_ACCESS_KEY_ENCRYPTION
SEMAPHORE_DB_PASSWORD=
SEMAPHORE_ADMIN=admin
SEMAPHORE_ADMIN_EMAIL=admin@example.com
SEMAPHORE_ADMIN_PASSWORD=
SEMAPHORE_ACCESS_KEY_ENCRYPTION=
# Absolute path to your local81 project (mounted into Semaphore as /srv/local81)
LOCAL81_DIR=../..
"""


# --- the fleet -------------------------------------------------------------


def _fleet_compose(catalog: Catalog) -> str:
    lines = [
        f"# {catalog.name} fleet — {len(catalog.recipes)} role containers, generated from the catalog.",
        "#   PUBKEY=\"$(cat ~/.ssh/id_demo.pub)\" docker compose -f docker-compose.fleet.yml up -d --build",
        f"name: {catalog.name}-fleet",
        "",
        "networks:",
        f"  {catalog.name}net:",
        f"    name: {catalog.name}net",
        "",
        "services:",
    ]
    for r in catalog.recipes:
        lines += [
            f"  {r.alias}:",
            "    build:",
            "      context: .",
            "      dockerfile: Dockerfile.role",
            "      args:",
            "        PUBKEY: ${PUBKEY:?export PUBKEY with your demo public key}",
            f"    image: {catalog.name}-role:latest",
            f"    container_name: {catalog.name}-{r.alias}",
            f"    hostname: {r.alias}",
            "    environment:",
            f"      ROLE: {r.role}",
            f"      WORKLOAD: {json.dumps(r.workload)}",
            f"    ports: [\"127.0.0.1:{r.port}:22\"]",
            f"    networks: [{catalog.name}net]",
            "    restart: \"no\"",
            f"    # {r.title}  |  health: {r.health or 'n/a'}",
            "",
        ]
    return "\n".join(lines)


# --- one-command bring-up + docs -------------------------------------------


def _start_script(catalog: Catalog, ui_port: int) -> str:
    return f"""#!/usr/bin/env bash
# Functional out of the box: (optionally) build the fleet, then start the
# click-first control panel. Run from this directory.
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$HERE"

if [ "${{1:-}}" = "--with-fleet" ]; then
  : "${{PUBKEY:?export PUBKEY=\\"$(cat ~/.ssh/your_key.pub)\\" to build the fleet}}"
  echo ">> building + launching the {len(catalog.recipes)}-host fleet ..."
  docker compose -f fleet/docker-compose.fleet.yml up -d --build
fi

echo ">> starting the control panel on http://127.0.0.1:{ui_port}"
echo ">> open that URL, then click your way through. Ctrl-C to stop."
exec python3 control_server.py --port {ui_port}
"""


def _stack_readme(catalog: Catalog, semaphore_port: int, ui_port: int) -> str:
    return f"""# Local-81 control surface — {catalog.name}

Generated by `local81 ui stack-render`. Two ways to drive the same `local81`
CLI; pick one.

## 1. Push-button (click-first, no typing)

```bash
./start.sh                 # or: ./start.sh --with-fleet  (also builds the demo hosts)
# open http://127.0.0.1:{ui_port}
```

`control_server.py` (stdlib only, binds localhost) serves `launcher.html` — an
accessible, click-through panel: pick a category, an action, a host group, press
the big button. Numeric options are +/− steppers, choices are buttons; the only
time you ever type is a rollback run-id. Actions that change hosts ask for a
one-tap confirmation. Output comes back in the page. Every button runs the
matching `dispatch/<category>.sh`, i.e. exactly the `local81` command you could
type by hand — the web layer can't do more than the CLI already can.

## 2. Multi-user (Semaphore + PostgreSQL 17)

```bash
cp .env.example .env        # fill blanks from your secret references
docker compose -f docker-compose.semaphore.yml --env-file .env up -d
# Semaphore on http://localhost:{semaphore_port}
```

For RBAC / LDAP / OIDC and an audit of who-ran-what. Import the task templates
from `local81 ui semaphore-render --catalog ...` (survey dropdowns front the
same dispatchers).

## The fleet to deploy to

```bash
PUBKEY="$(cat ~/.ssh/id_demo.pub)" \\
  docker compose -f fleet/docker-compose.fleet.yml up -d --build   # all {len(catalog.recipes)} roles
# or one role at a time:
PUBKEY=... bash build/web.sh
```

## Honesty notes

- No secret literals are written here; `.env` placeholders come from your secret
  references at runtime, preserving secrets-never-at-rest.
- The control server binds `127.0.0.1` and only runs the pre-generated dispatch
  scripts, validated against `actions.json`. Put a reverse proxy with auth in
  front if you expose it beyond localhost.
- Mutating actions (deploy, rollback) are labelled and confirmed; the rest are
  read-only.
"""
