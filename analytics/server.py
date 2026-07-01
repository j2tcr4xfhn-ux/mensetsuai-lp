"""めんせつAI LP アナリティクス（自己完結・TrendReaction方式の流用）。

ファネル: lp_view → cta_click → inquiry_submit。CVR = inquiry_submit / lp_view。
B2C(アプリ) と B2B(塾向け) を `lp` で識別し、ダッシュボードで分けて見られる。

プライバシー方針（TrendReaction踏襲）:
- ボット(UA判定)・オーナー(LP側 ?owner=1 で除外)は計測しない
- 流入元はドメイン単位ラベルのみ／IPは30日で自動削除／トラッキングCookie不使用

起動:
    pip install -r requirements.txt
    ANALYTICS_TOKEN=秘密 LP_ORIGIN=https://<your>.github.io uvicorn server:app --host 0.0.0.0 --port 8090
ダッシュボード: https://<host>/analytics?token=秘密  （&lp=b2c / &lp=b2b で絞り込み）
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

DB = os.environ.get("ANALYTICS_DB", "analytics.db")
TOKEN = os.environ.get("ANALYTICS_TOKEN", "change-me")
LP_ORIGIN = os.environ.get("LP_ORIGIN", "*")
OWNER_COOKIE = "ma_owner"
EVENTS = ("lp_view", "cta_click", "inquiry_submit")
LP_LABEL = {"b2c": "アプリ(B2C)", "b2b": "塾向け(B2B)", "": "—"}

app = FastAPI(title="mensetsuAI LP analytics")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[LP_ORIGIN] if LP_ORIGIN != "*" else ["*"],
    allow_methods=["POST", "GET"], allow_headers=["*"],
)

_BOT_UA = ("bot", "crawler", "spider", "slurp", "bingpreview", "headless",
           "curl", "wget", "python-requests", "facebookexternalhit", "embedly")
_REF_MAP = [
    (("t.co", "twitter.com", "x.com"), "X / Twitter"), (("instagram.com",), "Instagram"),
    (("google.",), "Google"), (("yahoo.", "search.yahoo"), "Yahoo!"), (("bing.com",), "Bing"),
    (("youtube.com", "youtu.be"), "YouTube"), (("tiktok.com",), "TikTok"),
    (("facebook.com",), "Facebook"), (("line.me", "liff.line"), "LINE"),
    (("note.com",), "note"), (("reddit.com",), "Reddit"),
]


def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _init():
    with _db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS metrics(day TEXT, name TEXT, n INTEGER, PRIMARY KEY(day,name))")
        c.execute("""CREATE TABLE IF NOT EXISTS events(
            ts INTEGER, name TEXT, ref TEXT, device TEXT, os TEXT, country TEXT, src TEXT, ip TEXT, lp TEXT)""")
        # 既存DBへの lp 列マイグレーション
        cols = [r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()]
        if "lp" not in cols:
            c.execute("ALTER TABLE events ADD COLUMN lp TEXT")


_init()


def _today():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _bump(name):
    with _db() as c:
        c.execute("INSERT INTO metrics(day,name,n) VALUES(?,?,1) "
                  "ON CONFLICT(day,name) DO UPDATE SET n=n+1", (_today(), name))


def is_bot(ua):
    u = (ua or "").lower()
    return not u or any(b in u for b in _BOT_UA)


def ref_label(ref, host_self):
    if not ref:
        return "直接 / その他"
    try:
        host = (urlsplit(ref).hostname or "").lower()
    except Exception:
        return "その他"
    if not host or host == host_self or host.endswith("github.io"):
        return "直接 / サイト内"
    for needles, label in _REF_MAP:
        if any(n in host for n in needles):
            return label
    return host.removeprefix("www.")


def device(ua):
    u = ua.lower()
    if "ipad" in u or ("tablet" in u and "mobile" not in u):
        return "タブレット"
    if "mobi" in u or "iphone" in u or "android" in u:
        return "スマホ"
    return "PC"


def os_name(ua):
    u = ua.lower()
    if "iphone" in u or "ipad" in u or "ios" in u:
        return "iOS"
    if "android" in u:
        return "Android"
    if "windows" in u:
        return "Windows"
    if "mac os" in u or "macintosh" in u:
        return "macOS"
    if "linux" in u:
        return "Linux"
    return "その他"


def client_ip(request):
    for h in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.post("/api/track")
async def track(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str(body.get("event") or "")
    if name not in EVENTS:
        return JSONResponse({"ok": False}, status_code=400)
    ua = request.headers.get("user-agent") or ""
    if is_bot(ua) or request.cookies.get(OWNER_COOKIE) == "1":
        return JSONResponse({"ok": True, "skipped": True})
    lp = str(body.get("lp") or "")
    if lp not in ("b2c", "b2b"):
        lp = ""
    _bump(name)
    with _db() as c:
        c.execute("INSERT INTO events(ts,name,ref,device,os,country,src,ip,lp) VALUES(?,?,?,?,?,?,?,?,?)",
                  (int(time.time()), name,
                   ref_label(str(body.get("ref") or ""), (request.url.hostname or "").lower()),
                   device(ua), os_name(ua),
                   (request.headers.get("cf-ipcountry") or "").upper(),
                   str(body.get("s") or "")[:40], client_ip(request), lp))
    return JSONResponse({"ok": True})


@app.get("/api/owner-exclude")
async def owner_exclude(token: str = ""):
    if token != TOKEN:
        raise HTTPException(403, "forbidden")
    r = Response("このブラウザを計測から除外しました。")
    r.set_cookie(OWNER_COOKIE, "1", max_age=400 * 86400, samesite="lax")
    return r


@app.get("/analytics", response_class=HTMLResponse)
async def dashboard(token: str = "", lp: str = "all"):
    if token != TOKEN:
        raise HTTPException(403, "?token=... が必要です")
    lpf = lp if lp in ("b2c", "b2b") else "all"
    cond = " AND lp=?" if lpf != "all" else ""
    ap = (lpf,) if lpf != "all" else ()
    days = 14
    since_ts = int(time.time()) - days * 86400

    with _db() as c:
        # IP 30日削除
        c.execute("UPDATE events SET ip='(削除済)' WHERE ts < ? AND ip!='(削除済)'", (int(time.time()) - 30 * 86400,))

        trows = c.execute(f"SELECT name, COUNT(*) n FROM events WHERE ts>=?{cond} GROUP BY name", (since_ts,) + ap).fetchall()
        totals = {e: 0 for e in EVENTS}
        for r in trows:
            if r["name"] in totals:
                totals[r["name"]] = r["n"]

        srows = c.execute(f"SELECT strftime('%Y-%m-%d', ts,'unixepoch','localtime') d, COUNT(*) n "
                          f"FROM events WHERE name='lp_view' AND ts>=?{cond} GROUP BY d", (since_ts,) + ap).fetchall()
        series = {r["d"]: r["n"] for r in srows}

        def brk(col):
            q = c.execute(f"SELECT {col} k, COUNT(*) n FROM events WHERE name='lp_view' AND ts>=?{cond} "
                          f"GROUP BY {col} ORDER BY n DESC LIMIT 8", (since_ts,) + ap).fetchall()
            tot = sum(x["n"] for x in q) or 1
            return [{"k": x["k"] or "—", "n": x["n"], "pct": round(x["n"] / tot * 100)} for x in q]

        refs, devs, srcs, oses, countries = brk("ref"), brk("device"), brk("src"), brk("os"), brk("country")
        hrows = c.execute(f"SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INTEGER) h, COUNT(*) n "
                          f"FROM events WHERE name='lp_view' AND ts>=?{cond} GROUP BY h", (since_ts,) + ap).fetchall()
        hours = [0] * 24
        for r in hrows:
            if r["h"] is not None:
                hours[r["h"]] = r["n"]
        # LP別内訳（フィルタ無視で全体を出す）
        lprows = c.execute("SELECT lp k, COUNT(*) n FROM events WHERE name='lp_view' AND ts>=? GROUP BY lp ORDER BY n DESC",
                           (since_ts,)).fetchall()
        lpt = sum(x["n"] for x in lprows) or 1
        lpbrk = [{"k": LP_LABEL.get(x["k"] or "", x["k"] or "—"), "n": x["n"], "pct": round(x["n"] / lpt * 100)} for x in lprows]
        recent = c.execute(f"SELECT ts,name,ref,device,country,src,lp FROM events WHERE 1=1{cond} "
                           f"ORDER BY ts DESC LIMIT 40", ap).fetchall()

    lpv, cta, inq = totals["lp_view"], totals["cta_click"], totals["inquiry_submit"]
    pct = lambda n, d: round(n / d * 100) if d else 0
    day_keys = [(datetime.now(timezone.utc).astimezone() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    lpser = [series.get(k, 0) for k in day_keys]
    cmax = max(lpser + [1])
    hmax = max(hours + [1])

    def bars(rows):
        return "".join(
            f'<div class="b"><span>{r["k"]}</span><div class="t"><i style="width:{r["pct"]}%"></i></div>'
            f'<b>{r["n"]}（{r["pct"]}%）</b></div>' for r in rows) or '<p class="muted">データなし</p>'

    chart = "".join(
        f'<div class="col" title="{k[5:]}: {v}"><i style="height:{round(v/cmax*100)}%"></i><span>{k[8:]}</span></div>'
        for k, v in zip(day_keys, lpser))
    hourbars = "".join(
        f'<div class="col" title="{h}時: {v}"><i style="height:{round(v/hmax*100)}%"></i><span>{h}</span></div>'
        for h, v in enumerate(hours))
    recent_rows = "".join(
        f'<tr><td>{datetime.fromtimestamp(r["ts"]).strftime("%m/%d %H:%M")}</td><td>{LP_LABEL.get(r["lp"] or "","—")}</td>'
        f'<td>{r["name"]}</td><td>{r["ref"]}</td><td>{r["device"]}</td><td>{r["country"] or "—"}</td><td>{r["src"] or "—"}</td></tr>'
        for r in recent) or '<tr><td colspan=7 class="muted">まだイベントがありません</td></tr>'

    def tab(v, label):
        on = (lpf == v)
        return (f'<a href="?token={token}&lp={v}" style="padding:7px 16px;border-radius:999px;'
                f'background:{"#4754d8" if on else "#11163a"};color:#fff;border:1px solid rgba(255,255,255,.15);'
                f'text-decoration:none;font-size:.85rem">{label}</a>')
    tabs = tab("all", "全体") + tab("b2c", "アプリ(B2C)") + tab("b2b", "塾向け(B2B)")

    html = f"""<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>めんせつAI LP アナリティクス</title><style>
body{{font-family:-apple-system,'Segoe UI',sans-serif;background:#0b0e23;color:#eef1ff;margin:0;padding:28px;line-height:1.7}}
.wrap{{max-width:920px;margin:0 auto}}h1{{font-size:1.3rem}}.muted{{color:#a6acd6}}code{{background:#11163a;padding:1px 6px;border-radius:5px}}
.tabs{{display:flex;gap:8px;margin:14px 0}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:16px 0}}
.kpi{{background:#11163a;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:16px}}
.kpi .v{{font-size:1.8rem;font-weight:800}}.kpi .l{{color:#a6acd6;font-size:.82rem}}
.card{{background:#11163a;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:18px;margin:14px 0}}
.funnel{{display:flex;gap:10px}}.funnel .s{{flex:1;text-align:center;background:#1a2050;border-radius:10px;padding:12px}}
.funnel .s .v{{font-size:1.4rem;font-weight:800}}.funnel .s .r{{color:#7ee59a;font-size:.85rem}}
.chart{{display:flex;align-items:flex-end;gap:4px;height:120px}}.chart .col{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}}
.chart .col i{{width:70%;background:linear-gradient(180deg,#4754d8,#34C759);border-radius:4px 4px 0 0;min-height:2px}}.chart .col span{{font-size:.6rem;color:#a6acd6;margin-top:4px}}
.b{{display:grid;grid-template-columns:120px 1fr 110px;align-items:center;gap:10px;margin:7px 0;font-size:.9rem}}
.b .t{{background:#0b0e23;border-radius:6px;height:10px;overflow:hidden}}.b .t i{{display:block;height:100%;background:#4754d8}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.08)}}
@media(max-width:680px){{.kpis{{grid-template-columns:1fr 1fr}}.cols{{grid-template-columns:1fr}}}}
</style></head><body><div class=wrap>
<h1>めんせつAI LP アナリティクス <span class=muted style="font-size:.8rem">直近{days}日</span></h1>
<div class=tabs>{tabs}</div>
<div class=card style="border-color:rgba(52,199,89,.35)"><b>自分のアクセスを除外する</b><div class=muted style="font-size:.85rem;margin-top:4px">LPを <code>?owner=1</code> 付きで一度開くと、そのブラウザは以後カウントされません。</div></div>
<div class=kpis>
  <div class=kpi><div class=v>{lpv:,}</div><div class=l>LP閲覧</div></div>
  <div class=kpi><div class=v>{cta:,}</div><div class=l>CTAクリック</div></div>
  <div class=kpi><div class=v>{inq:,}</div><div class=l>問い合わせ(B2B)</div></div>
  <div class=kpi><div class=v style="color:#7ee59a">{pct(cta,lpv)}%</div><div class=l>CTR = CTA/閲覧</div></div>
</div>
<div class=card><b>LP別の閲覧（全体）</b>{bars(lpbrk)}</div>
<div class=card><b>ファネル</b><div class=funnel style="margin-top:12px">
  <div class=s><div class=v>{lpv:,}</div><div>LP閲覧</div><div class=r>100%</div></div>
  <div class=s><div class=v>{cta:,}</div><div>CTAクリック</div><div class=r>{pct(cta,lpv)}%</div></div>
  <div class=s><div class=v>{inq:,}</div><div>問い合わせ</div><div class=r>{pct(inq,lpv)}%</div></div>
</div></div>
<div class=card><b>LP閲覧の推移</b><div class=chart style="margin-top:12px">{chart}</div></div>
<div class=cols><div class=card><b>流入元（どこから来たか）</b>{bars(refs)}</div><div class=card><b>デバイス</b>{bars(devs)}</div></div>
<div class=cols><div class=card><b>OS</b>{bars(oses)}</div><div class=card><b>国・地域</b>{bars(countries)}</div></div>
<div class=card><b>時間帯（LP閲覧・現地時間）</b><div class=chart style="margin-top:12px">{hourbars}</div></div>
<div class=card><b>流入タグ (?s=)</b><div class=muted style="font-size:.8rem">DMに ?s=記号 を付けると塾別の閲覧がわかる</div>{bars(srcs)}</div>
<div class=card><b>直近イベント（どんな人が見ているか）</b><table><tr><th>時刻</th><th>LP</th><th>種別</th><th>流入元</th><th>端末</th><th>国</th><th>タグ</th></tr>{recent_rows}</table></div>
<p class=muted style="font-size:.78rem">ボット・オーナー除外済み。流入元はドメイン単位。IPは30日で自動削除。トラッキングCookie不使用。</p>
</div></body></html>"""
    return HTMLResponse(html)


@app.get("/api/health")
async def health():
    return {"ok": True}
