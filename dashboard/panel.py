"""Panel de chats — API para el front Vite (persistente + seguro) y vista legacy.

Seguridad: login con contraseña -> token firmado (HMAC-SHA256, sin dependencias).
El front (Vite) guarda el token y lo manda como `Authorization: Bearer <token>`.

Rutas nuevas (para el front Vite, datos de Postgres, persistentes):
  POST /api/panel/login            {password} -> {token, exp}
  GET  /api/panel/chats            (Bearer)   -> [{numero, vendedor, n, last_ts, last_msg}]
  GET  /api/panel/chats/{numero}   (Bearer)   -> {numero, vendedor, mensajes:[...]}

Ruta legacy (vista rápida en vivo desde Redis, token por query):
  GET  /panel?token=...            página HTML simple
  GET  /panel/api/chats?token=...  JSON desde Redis
"""
import os
import json
import time
import hmac
import base64
import hashlib
from datetime import timezone


def _iso_utc(dt):
    """Marca un datetime naive (guardado en UTC) como UTC, para que el front lo
    convierta bien a hora de Lima. Si ya tiene tz, lo respeta."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

from fastapi import APIRouter, Query, Header, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from codigo_obsoleto.orchestrator import context
from codigo_obsoleto.shared import auth
from db import models

router_panel = APIRouter()

PANEL_TOKEN    = os.getenv("PANEL_TOKEN", "catu2026")            # legacy (Redis view)
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "catusita2026")     # login del front
PANEL_SECRET   = os.getenv("PANEL_SECRET", "cambia-esto-en-prod")
TOKEN_TTL      = 12 * 3600                                        # 12 horas


# ─── Token firmado (HMAC, sin librerías externas) ─────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(PANEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def _verify(token: str) -> bool:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(PANEL_SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return False
        payload = json.loads(_unb64(body))
        return payload.get("exp", 0) > time.time()
    except Exception:
        return False


def _auth(authorization: str = Header("")) -> None:
    token = authorization.replace("Bearer", "").strip()
    if not _verify(token):
        raise HTTPException(status_code=401, detail="no autorizado")


def _vendedor(numero: str) -> str:
    perfil = auth._MOCK_ASESORES.get(numero)
    return perfil.get("nombre", "") if perfil else ""


# ─── API nueva (front Vite, Postgres) ─────────────────────────────────────────

@router_panel.post("/api/panel/login")
async def login(body: dict = Body(...)):
    if (body or {}).get("password") != PANEL_PASSWORD:
        raise HTTPException(status_code=401, detail="contraseña incorrecta")
    exp = int(time.time()) + TOKEN_TTL
    return {"token": _sign({"exp": exp}), "exp": exp}


@router_panel.get("/api/panel/chats")
async def api_panel_chats(authorization: str = Header("")):
    _auth(authorization)
    try:
        chats = await models.list_chats()
    except Exception:
        chats = []
    for c in chats:
        c["vendedor"] = _vendedor(c["numero"])
        if c.get("last_ts"):
            c["last_ts"] = _iso_utc(c["last_ts"])
    return {"chats": chats, "total": len(chats)}


@router_panel.get("/api/panel/chats/{numero}")
async def api_panel_chat(numero: str, authorization: str = Header("")):
    _auth(authorization)
    try:
        msgs = await models.get_chat_messages(numero)
    except Exception:
        msgs = []
    for m in msgs:
        if m.get("created_at"):
            m["created_at"] = _iso_utc(m["created_at"])
    return {"numero": numero, "vendedor": _vendedor(numero), "mensajes": msgs}


# ─── Estadísticas (front Vite, Postgres) ─────────────────────────────────────

@router_panel.get("/api/panel/vendedores")
async def api_vendedores(authorization: str = Header("")):
    _auth(authorization)
    try:
        return {"vendedores": await models.list_vendedores()}
    except Exception:
        return {"vendedores": []}


@router_panel.get("/api/panel/stats/resumen")
async def api_resumen(authorization: str = Header(""),
                      vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return await models.stats_resumen(vendedor_id, desde, hasta)
    except Exception:
        return {"mensajes_totales": 0, "conversaciones": 0}


@router_panel.get("/api/panel/stats/evolucion")
async def api_evolucion(authorization: str = Header(""),
                        vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return {"data": await models.stats_evolucion(vendedor_id, desde, hasta)}
    except Exception:
        return {"data": []}


@router_panel.get("/api/panel/stats/por-dia")
async def api_por_dia(authorization: str = Header(""),
                      vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return {"data": await models.stats_por_dia(vendedor_id, desde, hasta)}
    except Exception:
        return {"data": []}


@router_panel.get("/api/panel/stats/por-hora")
async def api_por_hora(authorization: str = Header(""),
                       vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return {"data": await models.stats_por_hora(vendedor_id, desde, hasta)}
    except Exception:
        return {"data": []}


@router_panel.get("/api/panel/stats/tools")
async def api_tools(authorization: str = Header(""),
                    vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return {"data": await models.stats_tools(vendedor_id, desde, hasta)}
    except Exception:
        return {"data": []}


@router_panel.get("/api/panel/stats/ranking")
async def api_ranking(authorization: str = Header(""),
                      vendedor_id: str = Query(None), desde: str = Query(None), hasta: str = Query(None)):
    _auth(authorization)
    try:
        return {"data": await models.stats_ranking(vendedor_id, desde, hasta)}
    except Exception:
        return {"data": []}


@router_panel.get("/api/panel/stats/sin-uso")
async def api_sin_uso(authorization: str = Header("")):
    _auth(authorization)
    try:
        return {"data": await models.stats_sin_uso()}
    except Exception:
        return {"data": []}


# ─── Vista legacy (Redis, token por query) ────────────────────────────────────

@router_panel.get("/panel/api/chats")
async def api_chats(token: str = Query("")):
    if token != PANEL_TOKEN:
        return JSONResponse({"error": "token inválido"}, status_code=403)
    chats = await context.list_conversations()
    for c in chats:
        c["vendedor"] = _vendedor(c["numero"])
        c["n"] = len(c.get("mensajes", []))
    chats.sort(key=lambda c: c["n"], reverse=True)
    return JSONResponse({"chats": chats, "total": len(chats)})


@router_panel.get("/panel", response_class=HTMLResponse)
async def panel(token: str = Query("")):
    if token != PANEL_TOKEN:
        return HTMLResponse(
            "<body style='font-family:sans-serif;padding:40px'>"
            "<h3>🔒 Token inválido</h3><p>Usá <code>/panel?token=TU_TOKEN</code></p></body>",
            status_code=403,
        )
    return HTMLResponse(_HTML.replace("__TOKEN__", token))


_HTML = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catu · Panel (legacy)</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0b141a; color:#e9edef; }
  header { background:#202c33; padding:12px 18px; display:flex; align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; }
  header .meta { color:#8696a0; font-size:13px; margin-left:auto; }
  .wrap { display:flex; height:calc(100vh - 46px); }
  .list { width:320px; border-right:1px solid #222d34; overflow-y:auto; flex-shrink:0; }
  .item { padding:12px 16px; border-bottom:1px solid #182229; cursor:pointer; }
  .item:hover, .item.sel { background:#202c33; }
  .item .n { font-weight:600; font-size:14px; }
  .item .v { color:#8696a0; font-size:12px; margin-top:2px; }
  .chat { flex:1; overflow-y:auto; padding:18px 8%; }
  .empty { color:#8696a0; text-align:center; margin-top:60px; }
  .msg { max-width:70%; padding:8px 11px; border-radius:8px; margin:6px 0; font-size:14px; white-space:pre-wrap; }
  .user { background:#202c33; margin-right:auto; }
  .assistant { background:#005c4b; margin-left:auto; }
  .role { font-size:10px; text-transform:uppercase; color:#8696a0; margin-bottom:2px; }
</style></head>
<body>
<header><h1>💬 Catu · Panel (legacy Redis)</h1><span class="meta" id="meta">cargando…</span></header>
<div class="wrap"><div class="list" id="list"></div>
<div class="chat" id="chat"><div class="empty">Elegí una conversación</div></div></div>
<script>
const TOKEN="__TOKEN__"; let data=[], sel=null;
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function lista(){document.getElementById('list').innerHTML=data.map(c=>`<div class="item ${sel===c.numero?'sel':''}" onclick="sel='${c.numero}';lista();chat();"><div class="n">${c.vendedor||c.numero}</div><div class="v">${c.numero} · ${c.n} msgs</div></div>`).join('')||'<div class="empty">Sin chats</div>';}
function chat(){const c=data.find(x=>x.numero===sel);document.getElementById('chat').innerHTML=c?c.mensajes.map(m=>`<div class="msg ${m.role}"><div class="role">${m.role==='user'?'vendedor':'Catu'}</div>${esc(m.content)}</div>`).join(''):'<div class="empty">Elegí una conversación</div>';}
async function load(){try{const r=await fetch('/panel/api/chats?token='+encodeURIComponent(TOKEN));const j=await r.json();data=j.chats||[];document.getElementById('meta').textContent=j.total+' chats · 5s';if(!sel&&data.length)sel=data[0].numero;lista();chat();}catch(e){}}
load();setInterval(load,5000);
</script></body></html>"""
