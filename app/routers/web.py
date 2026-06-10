"""Веб-сервис Matrix Engine: лендинг, все типы разборов, страница результата
с оглавлением/картой/озвучкой, админка багов. HTML — строками (без шаблонизатора).

Тяжёлые разборы считаются в фоне (BackgroundTasks) под заранее выданным pid,
страница со спиннером опрашивает /r/{pid} — так HTTP-запрос всегда короткий.
"""
from __future__ import annotations

import html
import re
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Form, Query
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import config, database, tts, viz
from ..calc import astrology
from ..engine import build_event, build_profile, build_synastry
from ..models import AnalysisType, ProfileRequest
from ..synthesis import CREDIBILITY, LOADING_MESSAGES, METHOD_BASIS

router = APIRouter(tags=["web"])

_web_status: dict[str, str] = {}  # pid -> "error:<text>" если фон упал

# Глифы и короткие поведенческие пояснения светил (для блока «Положение светил»).
_GLYPH = {
    "sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
    "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇",
    "ascendant": "Asc",
}
_GLOSS = {
    "sun": "ядро личности и воля — как ты светишь",
    "moon": "эмоции и внутренние реакции — что нужно для покоя",
    "mercury": "мышление и речь — как обрабатываешь информацию",
    "venus": "что любишь и как привязываешься",
    "mars": "энергия действия — как добиваешься и злишься",
    "jupiter": "рост и оптимизм — где расширяешься",
    "saturn": "дисциплина и границы — где зона напряжения",
    "uranus": "независимость — где ломаешь шаблоны",
    "neptune": "воображение и идеалы — где растворяешься",
    "pluto": "глубина и власть — где трансформируешься",
    "ascendant": "первое впечатление — твоя «маска»",
}
_HOUSE_RU = {1: "1-й", 2: "2-й", 3: "3-й", 4: "4-й", 5: "5-й", 6: "6-й",
             7: "7-й", 8: "8-й", 9: "9-й", 10: "10-й", 11: "11-й", 12: "12-й"}


def _modules_of(data: dict) -> dict:
    m = data.get("calculation_modules") or {}
    return m.get("person_a", m)


def _positions_html(data: dict) -> str:
    """Блок «Положение светил» в стиле Co-Star — точные позиции как доказательство."""
    w = (_modules_of(data).get("western_astrology") or {})
    if w.get("calculation_status") != "calculated":
        return ""
    items = []
    asc = w.get("ascendant") or {}
    if asc.get("sign"):
        items.append(("ascendant", "Асцендент", asc.get("sign"), int(float(asc.get("degree", 0))),
                      asc.get("minute", 0), 1, False))
    for p in w.get("positions") or []:
        items.append((p["key"], p["name"], p["sign"], int(float(p["degree"])),
                      p.get("minute", 0), p.get("house"), p.get("retrograde")))
    rows = []
    for key, name, sign, deg, mn, house, retro in items:
        rx = " <span class=rx>℞</span>" if retro else ""
        hh = f"<span class=house>{_HOUSE_RU.get(house, '')} дом</span>" if house else ""
        rows.append(
            f"<div class=pl><div class=glyph>{_GLYPH.get(key, '·')}</div>"
            f"<div class=plmain><div class=plname>{html.escape(name)}{rx}</div>"
            f"<div class=plpos>{html.escape(sign)} {deg}°{mn:02d}′ · {hh}</div>"
            f"<div class=plgloss>{html.escape(_GLOSS.get(key, ''))}</div></div></div>"
        )
    return (
        "<div class=sectionhead>Положение светил в момент рождения</div>"
        "<p class=note>Рассчитано по астрономическим эфемеридам — до угловой минуты. "
        "Это те же координаты, что вы видите в профессиональных астрономических картах.</p>"
        f"<div class=planets>{''.join(rows)}</div>"
    )

_ANALYSIS_LABELS = {
    "personality": "Личность — базовый код",
    "current_period": "Текущий период",
    "work": "Деньги и реализация",
}

_FEATURES = [
    ("🧬", "Личность", "Базовый код: сильные стороны, где сам себе мешаешь, главный внутренний конфликт."),
    ("🌗", "Текущий период", "Какая тема включена сейчас, где легко слить силу, окно возможностей."),
    ("💼", "Деньги и реализация", "Как ты зарабатываешь, как саботируешь, где профессиональная сила."),
    ("❤️", "Совместимость", "Резонанс двух кодов: где усиливаете, где триггерите, как восстановиться."),
    ("🤝", "Сделка / Событие", "Оценка конкретной даты: что она включает и стоит ли в неё входить."),
    ("🎨", "Карта и озвучка", "Визуальная карта профиля и аудио-разбор голосом — в один тап."),
]

_CSS = """
:root{--ink:#15161a;--muted:#6b7280;--line:#e7e8ec;--bg:#ffffff;--soft:#f7f7f9;
 --accent:#1a1a1d;--accent2:#0a0a0c;}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
 line-height:1.62;font-size:17px;-webkit-font-smoothing:antialiased;}
a{color:var(--accent);text-decoration:none;}a:hover{text-decoration:underline;}
.wrap{max-width:880px;margin:0 auto;padding:0 22px;}
.nav{display:flex;align-items:center;justify-content:space-between;padding:20px 0;
 border-bottom:1px solid var(--line);}
.brand{font-weight:700;font-size:18px;letter-spacing:-.01em;color:var(--ink);}
.brand span{color:var(--accent);}
.navlinks a{margin-left:20px;color:var(--muted);font-size:15px;}
.hero{padding:64px 0 26px;}
h1{font-size:clamp(30px,5vw,46px);font-weight:780;letter-spacing:-.025em;margin:0 0 14px;line-height:1.08;}
.lead{color:var(--muted);font-size:clamp(17px,2.4vw,21px);margin:0 0 26px;max-width:680px;}
.cta{display:inline-block;background:var(--accent2);color:#fff;padding:14px 26px;border-radius:11px;
 font-weight:640;font-size:16px;}
.cta:hover{background:#000;text-decoration:none;}
.cta.ghost{background:#fff;color:var(--ink);border:1px solid var(--line);margin-left:10px;}
.strip{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13.5px;
 padding:18px 0 8px;border-top:1px solid var(--line);margin-top:30px;}
.strip b{color:var(--ink);font-weight:600;}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin:26px 0;}
.feat{border:1px solid var(--line);border-radius:14px;padding:20px;background:#fff;}
.feat .ic{font-size:24px;}.feat h3{font-size:17px;margin:8px 0 5px;}
.feat p{color:var(--muted);font-size:14.5px;margin:0;}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:22px 0;}
.step{padding:18px 20px;border-radius:14px;background:var(--soft);}
.step .n{display:inline-flex;width:28px;height:28px;align-items:center;justify-content:center;
 background:var(--accent2);color:#fff;border-radius:50%;font-size:14px;font-weight:700;margin-bottom:8px;}
.step h3{font-size:16px;margin:4px 0 4px;}.step p{color:var(--muted);font-size:14px;margin:0;}
.sectionhead{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
 margin:48px 0 6px;font-weight:650;}
.formcard{border:1px solid var(--line);border-radius:16px;padding:26px;margin:14px 0 10px;}
label{display:block;font-size:14px;color:var(--muted);margin:14px 0 6px;}
input,select{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:10px;
 font-size:16px;background:#fff;color:var(--ink);}
input:focus,select:focus{outline:none;border-color:var(--accent);}
.row{display:flex;gap:14px;flex-wrap:wrap;}.row>div{flex:1;min-width:160px;}
button{margin-top:22px;width:100%;padding:14px 18px;border:0;border-radius:11px;
 background:var(--accent2);color:#fff;font-size:16px;font-weight:640;cursor:pointer;}
button:hover{background:#000;}
.note{color:var(--muted);font-size:13px;margin-top:10px;}
.tabbar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0;}
.tab{padding:9px 15px;border:1px solid var(--line);border-radius:999px;font-size:14px;color:var(--ink);}
.tab.on{background:var(--accent2);color:#fff;border-color:var(--accent2);}
.tab:hover{text-decoration:none;}
.cred{background:var(--soft);border:1px solid var(--line);border-radius:13px;padding:16px 18px;
 color:var(--muted);font-size:14.5px;margin:18px 0;}
.summary{font-size:19px;line-height:1.6;margin:8px 0 22px;}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 26px;}
.toc a{font-size:13.5px;padding:6px 12px;border:1px solid var(--line);border-radius:999px;color:var(--ink);}
.sec{border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin:16px 0;scroll-margin-top:18px;}
.sec h2{font-size:20px;font-weight:680;margin:0 0 8px;letter-spacing:-.01em;}
.sec p{margin:9px 0;}
.chart{display:block;max-width:100%;border:1px solid var(--line);border-radius:16px;margin:16px 0;}
.planets{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:14px 0 8px;}
.pl{display:flex;gap:14px;align-items:flex-start;border:1px solid var(--line);border-radius:14px;padding:14px 16px;}
.glyph{font-size:24px;line-height:1.2;width:30px;text-align:center;color:var(--accent);flex:none;}
.plname{font-weight:650;font-size:15px;}
.plpos{color:var(--ink);font-size:14px;margin-top:1px;}
.plgloss{color:var(--muted);font-size:13px;margin-top:3px;}
.house{color:var(--muted);}
.rx{color:#a11;font-size:12px;}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0;}
.btnlink{display:inline-block;padding:11px 18px;border:1px solid var(--line);border-radius:11px;
 color:var(--ink);font-size:15px;font-weight:560;}
.btnlink:hover{background:var(--soft);text-decoration:none;}
details{border:1px solid var(--line);border-radius:13px;padding:12px 16px;margin:18px 0;}
summary{cursor:pointer;font-weight:600;color:var(--muted);}
hr{border:0;border-top:1px solid var(--line);margin:22px 0;}
.foot{color:var(--muted);font-size:13px;margin:46px 0;padding-top:20px;border-top:1px solid var(--line);}
.spinner{width:36px;height:36px;border:3px solid var(--line);border-top-color:var(--accent2);
 border-radius:50%;margin:26px 0;animation:sp .8s linear infinite;}
@keyframes sp{to{transform:rotate(360deg)}}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:18px;}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top;}
th{color:var(--muted);font-weight:600;}
.kind{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#f3f4f6;}
.kind.exception{background:#fdecec;color:#a11;}.kind.ai_failure{background:#fff4e5;color:#9a5b00;}
.kind.lang_leak{background:#eef2ff;color:#3730a3;}
.stat{display:inline-block;margin-right:18px;color:var(--muted);font-size:14px;}
.stat b{color:var(--ink);font-size:18px;}
"""


def _page(title: str, body: str, head_extra: str = "") -> str:
    return (
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style>{head_extra}</head>"
        f"<body>{body}</body></html>"
    )


def _nav() -> str:
    return (
        "<div class=wrap><div class=nav><a class=brand href='/'>Матрица<span>.</span></a>"
        "<div class=navlinks><a href='/compat'>Совместимость</a>"
        "<a href='/event'>Сделка</a><a href='/proof'>Точность</a>"
        "<a href='/about'>Как это работает</a></div></div></div>"
    )


def _md_to_html(md: str) -> str:
    out: list[str] = []
    for line in (md or "").split("\n"):
        s = line.rstrip()
        if s.startswith("## "):
            out.append(f"<h2>{html.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            out.append(f"<h2>{html.escape(s[2:])}</h2>")
        elif s.strip() == "---":
            out.append("<hr>")
        elif not s.strip():
            continue
        else:
            esc = html.escape(s)
            esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
            if esc.lstrip().startswith("- "):
                esc = "• " + esc.lstrip()[2:]
            out.append(f"<p>{esc}</p>")
    return "\n".join(out)


def _spinner_page(pid: str, title: str, lead: str) -> str:
    import json as _json
    msgs = _json.dumps(LOADING_MESSAGES, ensure_ascii=False)
    body = (
        f"{_nav()}<div class=wrap><div class=hero><h1>{html.escape(title)}</h1>"
        f"<div class=spinner></div>"
        f"<p id=ld class=lead>{html.escape(lead)}</p></div></div>"
        f"<script>const M={msgs};let i=Math.floor(Math.random()*M.length);"
        "const e=document.getElementById('ld');"
        "function t(){e.textContent=M[i%M.length];i++;}t();setInterval(t,1500);</script>"
    )
    # Перезагрузка опрашивает результат; JS крутит статусы между перезагрузками.
    head = f"<meta http-equiv='refresh' content='6;url=/r/{pid}'>"
    return _page("Считаю…", body, head)


# ---------------- лендинг: чёрно-белый нуар на частицах ----------------
_STAGE_CSS = """
.stage{position:relative;height:96vh;min-height:600px;overflow:hidden;background:#f4f3ee;}
.stage:after{content:'';position:absolute;inset:0;pointer-events:none;
 box-shadow:inset 0 0 240px rgba(0,0,0,.22);}
.sky{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:crosshair;}
.stagenav{position:absolute;top:0;left:0;right:0;display:flex;justify-content:space-between;
 align-items:center;padding:22px 26px;z-index:2;}
.brandw{color:#0d0d0f;font-weight:800;font-size:18px;letter-spacing:.04em;text-decoration:none;text-transform:uppercase;}
.stagenav>div a{color:#0d0d0f;text-decoration:none;margin-left:22px;font-size:12px;text-transform:uppercase;letter-spacing:.1em;}
.stagehero{position:absolute;left:0;right:0;bottom:0;padding:46px 24px 58px;z-index:2;text-align:center;pointer-events:none;}
.stagehero>*{pointer-events:auto;}
.bigh{color:#0a0a0c;font-family:Georgia,'Times New Roman',serif;font-size:clamp(32px,6vw,66px);
 font-weight:700;letter-spacing:-.005em;margin:0 0 14px;line-height:1.0;text-transform:uppercase;}
.bigp{color:#46453f;font-size:clamp(14px,2vw,18px);margin:0 auto 24px;max-width:520px;}
.cta2{display:inline-block;background:#0a0a0c;color:#f4f3ee;padding:16px 36px;border-radius:0;
 font-weight:700;font-size:13px;letter-spacing:.14em;text-transform:uppercase;text-decoration:none;}
.cta2:hover{background:#000;}
.scrollhint{position:absolute;bottom:16px;left:0;right:0;text-align:center;color:#8f8e87;
 font-size:11px;letter-spacing:.18em;text-transform:uppercase;z-index:2;animation:bobh 1.9s ease-in-out infinite;}
@keyframes bobh{0%,100%{transform:translateY(0)}50%{transform:translateY(5px)}}
"""

_STAGE_HTML = """
<section class=stage>
  <canvas id=sky class=sky></canvas>
  <div class=stagenav>
    <a class=brandw href='/'>Матрица</a>
    <div><a href='/proof'>Точность</a><a href='/about'>Метод</a></div>
  </div>
  <div class=stagehero>
    <h1 class=bigh>Карта твоего<br>характера</h1>
    <p class=bigp>Тысячи точек собираются в небо момента твоего рождения. Проведи по нему.</p>
    <a class=cta2 href='#form'>Построить карту</a>
  </div>
  <div class=scrollhint>листай вниз</div>
</section>
"""

# Вращающийся 3D-глобус из чёрных точек + наклонные орбиты с планетами.
# Плавно (мягкий спринг + высокое демпфирование), нуар, без внешних библиотек.
_STAGE_JS = r"""
(function(){
  var cv=document.getElementById('sky'); if(!cv||!cv.getContext) return;
  var ctx=cv.getContext('2d'), W,H,DPR,cx,cy,SCALE,P=[],mx=-1e5,my=-1e5;
  var TX=0.42, angY=0, phi1=0, phi2=Math.PI;
  var R1=1.55,T1=0.55,R2=2.05,T2=-0.62;
  function rnd(a,b){return a+Math.random()*(b-a);}
  function fib(n){var p=[],ga=Math.PI*(3-Math.sqrt(5));
    for(var i=0;i<n;i++){var y=1-(i/(n-1))*2,r=Math.sqrt(Math.max(0,1-y*y)),th=ga*i;
      p.push({x:Math.cos(th)*r,y:y,z:Math.sin(th)*r});}return p;}
  function ringPt(rr,rt,th){var x=Math.cos(th)*rr,z=Math.sin(th)*rr,y=0,ct=Math.cos(rt),st=Math.sin(rt);
    return {x:x,y:y*ct-z*st,z:y*st+z*ct};}
  var sphere=fib(760), ring1=[], ring2=[], pl1=[], pl2=[];
  for(var i=0;i<96;i++)ring1.push(ringPt(R1,T1,i/96*6.2832));
  for(var i=0;i<116;i++)ring2.push(ringPt(R2,T2,i/116*6.2832));
  for(var i=0;i<18;i++)pl1.push({x:rnd(-.12,.12),y:rnd(-.12,.12),z:rnd(-.12,.12)});
  for(var i=0;i<18;i++)pl2.push({x:rnd(-.1,.1),y:rnd(-.1,.1),z:rnd(-.1,.1)});
  var COUNT=sphere.length+ring1.length+ring2.length+pl1.length+pl2.length;
  function build(){
    DPR=Math.min(window.devicePixelRatio||1,2);
    W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
    cx=W/2;cy=H*0.42;SCALE=Math.min(W,H)*0.135;
    var dust=Math.round(W*H/16000),N=COUNT+dust,prev=P;P=[];
    for(var i=0;i<N;i++){var sx=prev[i]?prev[i].x:rnd(0,W),sy=prev[i]?prev[i].y:rnd(0,H);
      P.push({x:sx,y:sy,vx:0,vy:0,dust:i>=COUNT,ph:Math.random()*6.28,bx:rnd(0,W),by:rnd(0,H)});}
  }
  function proj(q){
    var ca=Math.cos(angY),sa=Math.sin(angY);
    var x=q.x*ca+q.z*sa, z=-q.x*sa+q.z*ca, y=q.y;
    var ct=Math.cos(TX),st=Math.sin(TX), y2=y*ct-z*st, z2=y*st+z*ct;
    return [cx+x*SCALE, cy-y2*SCALE, z2];
  }
  var idx=0;
  function put(tx,ty,al,sz){
    var p=P[idx++]; if(!p)return;
    var ax=(tx-p.x)*0.014, ay=(ty-p.y)*0.014;
    var dx=p.x-mx,dy=p.y-my,d2=dx*dx+dy*dy,RAD=120;
    if(d2<RAD*RAD){var d=Math.sqrt(d2)||1,f=(RAD-d)/RAD*4.2;ax+=dx/d*f;ay+=dy/d*f;}
    p.vx=(p.vx+ax)*0.91; p.vy=(p.vy+ay)*0.91; p.x+=p.vx; p.y+=p.vy;
    ctx.globalAlpha=al; ctx.fillRect(p.x,p.y,sz,sz);
  }
  function frame(ts){
    ctx.clearRect(0,0,W,H);
    angY+=0.0042; phi1+=0.010; phi2-=0.0075;
    ctx.fillStyle='#121214'; idx=0;
    var i,q,dn;
    for(i=0;i<sphere.length;i++){q=proj(sphere[i]);dn=(q[2]/2.1+1)/2;put(q[0],q[1],0.14+0.5*dn,q[2]>0?1.5:1.0);}
    for(i=0;i<ring1.length;i++){q=proj(ring1[i]);dn=(q[2]/2.1+1)/2;put(q[0],q[1],0.10+0.4*dn,1.1);}
    for(i=0;i<ring2.length;i++){q=proj(ring2[i]);dn=(q[2]/2.1+1)/2;put(q[0],q[1],0.10+0.4*dn,1.1);}
    var c1=ringPt(R1,T1,phi1);
    for(i=0;i<pl1.length;i++){q=proj({x:c1.x+pl1[i].x,y:c1.y+pl1[i].y,z:c1.z+pl1[i].z});put(q[0],q[1],0.92,1.7);}
    var c2=ringPt(R2,T2,phi2);
    for(i=0;i<pl2.length;i++){q=proj({x:c2.x+pl2[i].x,y:c2.y+pl2[i].y,z:c2.z+pl2[i].z});put(q[0],q[1],0.92,1.7);}
    for(i=idx;i<P.length;i++){var p=P[i];
      var tx=p.bx+Math.cos(ts*0.00026+p.ph)*38, ty=p.by+Math.sin(ts*0.00032+p.ph)*38;
      var ax=(tx-p.x)*0.01, ay=(ty-p.y)*0.01;
      var dx=p.x-mx,dy=p.y-my,d2=dx*dx+dy*dy;
      if(d2<120*120){var d=Math.sqrt(d2)||1,f=(120-d)/120*3.4;ax+=dx/d*f;ay+=dy/d*f;}
      p.vx=(p.vx+ax)*0.91;p.vy=(p.vy+ay)*0.91;p.x+=p.vx;p.y+=p.vy;
      ctx.globalAlpha=0.11;ctx.fillRect(p.x,p.y,1,1);
    }
    ctx.globalAlpha=1; requestAnimationFrame(frame);
  }
  function move(e){var r=cv.getBoundingClientRect();var t=e.touches?e.touches[0]:e;mx=t.clientX-r.left;my=t.clientY-r.top;}
  function leave(){mx=-1e5;my=-1e5;}
  window.addEventListener('resize',build);
  cv.addEventListener('mousemove',move);cv.addEventListener('mouseleave',leave);
  cv.addEventListener('touchmove',function(e){move(e);},{passive:true});
  cv.addEventListener('touchend',leave);
  build();requestAnimationFrame(frame);
})();
"""


@router.get("/", response_class=HTMLResponse)
def landing() -> str:
    opts = "".join(f"<option value='{k}'>{html.escape(v)}</option>" for k, v in _ANALYSIS_LABELS.items())
    feats = "".join(
        f"<div class=feat><div class=ic>{ic}</div><h3>{html.escape(t)}</h3><p>{html.escape(d)}</p></div>"
        for ic, t, d in _FEATURES
    )
    content = f"""
    <div class=wrap>
      <div class=sectionhead>Что внутри</div>
      <div class=grid>{feats}</div>

      <div class=sectionhead>Как это работает</div>
      <div class=steps>
        <div class=step><div class=n>1</div><h3>Данные</h3><p>Дата, время и город рождения — точные параметры момента.</p></div>
        <div class=step><div class=n>2</div><h3>Расчёт</h3><p>Положение светил считается до угловой минуты, детерминированно.</p></div>
        <div class=step><div class=n>3</div><h3>Разбор</h3><p>Выводы — на языке поведения: сценарии, риски, что делать.</p></div>
      </div>

      <div class=sectionhead id=form>Получить разбор</div>
      <div class=formcard>
        <div class=tabbar>
          <span class='tab on'>Личность / период / деньги</span>
          <a class=tab href='/compat'>Совместимость</a>
          <a class=tab href='/event'>Сделка / событие</a>
        </div>
        <form method=post action='/report'>
          <label>Имя</label>
          <input name=name placeholder='Как тебя зовут' required>
          <label>Дата рождения</label>
          <input name=birth_date type=date required>
          <div class=row>
            <div><label>Время рождения (по желанию)</label><input name=birth_time placeholder='14:30'></div>
            <div><label>Город рождения (по желанию)</label><input name=birth_place placeholder='Москва'></div>
          </div>
          <label>Что смотрим</label>
          <select name=analysis_type>{opts}</select>
          <button type=submit>Показать мой код</button>
          <p class=note>Время и город нужны для более тонкого слоя — без них тоже работает.</p>
        </form>
      </div>

      <p class=foot>Важные решения о здоровье, деньгах и отношениях вы принимаете сами.
      · <a href='/proof'>Откуда точность →</a></p>
    </div>"""
    body = _STAGE_HTML + content + "<script>" + _STAGE_JS + "</script>"
    return _page("Матрица — поведенческий профайлинг", body, "<style>" + _STAGE_CSS + "</style>")


@router.get("/about", response_class=HTMLResponse)
def about() -> str:
    body = (f"{_nav()}<div class=wrap><div class=hero><h1>Как это работает</h1></div>"
            f"<div class=sec>{_md_to_html(METHOD_BASIS)}</div>"
            "<p class=foot><a href='/proof'>Доказательство точности →</a> · "
            "<a href='/#form'>к разбору</a></p></div>")
    return _page("Как это работает · Матрица", body)


@router.get("/proof", response_class=HTMLResponse)
def proof() -> str:
    """Маркетинговый экран: откуда берётся точность + живой расчёт-образец."""
    geo = {"lat": 51.4769, "lon": 0.0, "timezone": "UTC"}  # Гринвич, эпоха J2000
    sample = {"calculation_modules": {"western_astrology": astrology.western(date(2000, 1, 1), "12:00", geo)}}
    table = _positions_html(sample)
    body = f"""{_nav()}
    <div class=wrap>
      <div class=hero>
        <h1>Откуда точность</h1>
        <p class=lead>Положения светил считает <b>Swiss Ephemeris</b> (Astrodienst) на базе
        <b>NASA JPL DE431</b> — тех же эфемерид, что используют в профессиональной астрономии
        и расчёте траекторий космических аппаратов.</p>
        <div class=strip>
          <span>🛰 <b>NASA JPL DE431</b></span>
          <span>🧮 <b>Детерминированно</b> — одна дата всегда даёт один результат</span>
          <span>🎯 <b>Точность до угловой минуты</b></span>
        </div>
      </div>
      <div class=sec>
        <h2>Что это значит на практике</h2>
        <p>Это не «приблизительный гороскоп». Это воспроизводимый астрономический расчёт:
        мы берём дату, время и координаты — и получаем положение каждого светила с точностью
        до угловой минуты. Те же координаты вы увидите в любом профессиональном источнике
        (Astro.com, Co-Star) — мы сверяли: совпадение до угловой минуты.</p>
      </div>
      <div class=sectionhead>Живой образец расчёта — эпоха J2000 (1 января 2000, 12:00 UTC, Гринвич)</div>
      {table}
      <p class=note>Это рассчитано прямо сейчас этим же движком. Любую дату можно проверить
      против профессионального эфемеридного источника — числа совпадут.</p>
      <p class=foot><a class=cta href='/#form'>Построить мою карту</a></p>
    </div>"""
    return _page("Доказательство точности · Матрица", body)


# ---------------- основной разбор ----------------
def _build_and_save(pid: str, req: ProfileRequest, where: str) -> None:
    try:
        profile = build_profile(req)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", where, f"{type(e).__name__}: {e}")


@router.post("/report", response_class=HTMLResponse)
def report(
    background_tasks: BackgroundTasks,
    name: str = Form(""),
    birth_date: str = Form(...),
    birth_time: str = Form(""),
    birth_place: str = Form(""),
    analysis_type: str = Form("personality"),
) -> str:
    try:
        bd = date.fromisoformat(birth_date.strip())
    except ValueError:
        return _page("Ошибка", f"{_nav()}<div class=wrap><h1>Неверная дата</h1>"
                     "<p>Формат: ГГГГ-ММ-ДД.</p><a href='/'>← назад</a></div>")
    try:
        atype = AnalysisType(analysis_type)
    except ValueError:
        atype = AnalysisType.personality
    req = ProfileRequest(
        name=name.strip(), birth_date=bd,
        birth_time=(birth_time.strip() or None), birth_place=(birth_place.strip() or None),
        main_request=_ANALYSIS_LABELS.get(analysis_type, ""), analysis_type=atype,
    )
    pid = uuid.uuid4().hex
    background_tasks.add_task(_build_and_save, pid, req, "web/report")
    return _spinner_page(pid, "Считаю твой код…",
                         "Беру астрономические параметры момента рождения и перевожу в поведенческий профиль. До минуты.")


# ---------------- совместимость ----------------
@router.get("/compat", response_class=HTMLResponse)
def compat_form() -> str:
    body = f"""{_nav()}
    <div class=wrap>
      <div class=sectionhead id=form>Совместимость — резонанс двух кодов</div>
      <div class=formcard>
        <div class=tabbar>
          <a class=tab href='/#form'>Личность / период / деньги</a>
          <span class='tab on'>Совместимость</span>
          <a class=tab href='/event'>Сделка / событие</a>
        </div>
        <form method=post action='/compat/run'>
          <div class=row>
            <div><label>Твоё имя</label><input name=name_a required></div>
            <div><label>Твоя дата рождения</label><input name=date_a type=date required></div>
          </div>
          <div class=row>
            <div><label>Твоё время (по желанию)</label><input name=time_a placeholder='14:30'></div>
            <div><label>Твой город (по желанию)</label><input name=place_a placeholder='Москва'></div>
          </div>
          <hr>
          <div class=row>
            <div><label>Имя партнёра</label><input name=name_b required></div>
            <div><label>Дата партнёра</label><input name=date_b type=date required></div>
          </div>
          <div class=row>
            <div><label>Время партнёра (по желанию)</label><input name=time_b placeholder='09:15'></div>
            <div><label>Город партнёра (по желанию)</label><input name=place_b placeholder='Казань'></div>
          </div>
          <button type=submit>Показать резонанс</button>
          <p class=note>Это карта, где вы усиливаете и где триггерите друг друга — без приговора «вместе/нет».</p>
        </form>
      </div>
    </div>"""
    return _page("Совместимость · Матрица", body)


def _build_synastry_and_save(pid: str, a: ProfileRequest, b: ProfileRequest) -> None:
    try:
        profile = build_synastry(a, b)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", "web/compat", f"{type(e).__name__}: {e}")


@router.post("/compat/run", response_class=HTMLResponse)
def compat_run(
    background_tasks: BackgroundTasks,
    name_a: str = Form(""), date_a: str = Form(...), time_a: str = Form(""), place_a: str = Form(""),
    name_b: str = Form(""), date_b: str = Form(...), time_b: str = Form(""), place_b: str = Form(""),
) -> str:
    try:
        bda, bdb = date.fromisoformat(date_a.strip()), date.fromisoformat(date_b.strip())
    except ValueError:
        return _page("Ошибка", f"{_nav()}<div class=wrap><h1>Неверная дата</h1><a href='/compat'>← назад</a></div>")
    a = ProfileRequest(name=name_a.strip(), birth_date=bda, birth_time=(time_a.strip() or None),
                       birth_place=(place_a.strip() or None), analysis_type=AnalysisType.compatibility)
    b = ProfileRequest(name=name_b.strip(), birth_date=bdb, birth_time=(time_b.strip() or None),
                       birth_place=(place_b.strip() or None), analysis_type=AnalysisType.compatibility)
    pid = uuid.uuid4().hex
    background_tasks.add_task(_build_synastry_and_save, pid, a, b)
    return _spinner_page(pid, "Считаю ваш резонанс…",
                         "Сравниваю два кода: где усиливаете и где триггерите друг друга. До минуты.")


# ---------------- сделка / событие ----------------
@router.get("/event", response_class=HTMLResponse)
def event_form() -> str:
    body = f"""{_nav()}
    <div class=wrap>
      <div class=sectionhead id=form>Сделка / событие — стоит ли в эту дату</div>
      <div class=formcard>
        <div class=tabbar>
          <a class=tab href='/#form'>Личность / период / деньги</a>
          <a class=tab href='/compat'>Совместимость</a>
          <span class='tab on'>Сделка / событие</span>
        </div>
        <form method=post action='/event/run'>
          <div class=row>
            <div><label>Имя</label><input name=name required></div>
            <div><label>Дата рождения</label><input name=birth_date type=date required></div>
          </div>
          <div class=row>
            <div><label>Время рождения (по желанию)</label><input name=birth_time placeholder='14:30'></div>
            <div><label>Город рождения (по желанию)</label><input name=birth_place placeholder='Москва'></div>
          </div>
          <hr>
          <div class=row>
            <div><label>Дата события</label><input name=event_date type=date required></div>
            <div><label>Что за событие</label><input name=event_desc placeholder='подписание сделки' required></div>
          </div>
          <button type=submit>Оценить дату</button>
          <p class=note>Вероятностный вывод: скорее благоприятно / нейтрально / лучше перенести. Не финансовый совет.</p>
        </form>
      </div>
    </div>"""
    return _page("Сделка / событие · Матрица", body)


def _build_event_and_save(pid: str, req: ProfileRequest, ev: date, desc: str) -> None:
    try:
        profile = build_event(req, ev, desc)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", "web/event", f"{type(e).__name__}: {e}")


@router.post("/event/run", response_class=HTMLResponse)
def event_run(
    background_tasks: BackgroundTasks,
    name: str = Form(""), birth_date: str = Form(...), birth_time: str = Form(""), birth_place: str = Form(""),
    event_date: str = Form(...), event_desc: str = Form(""),
) -> str:
    try:
        bd, ev = date.fromisoformat(birth_date.strip()), date.fromisoformat(event_date.strip())
    except ValueError:
        return _page("Ошибка", f"{_nav()}<div class=wrap><h1>Неверная дата</h1><a href='/event'>← назад</a></div>")
    req = ProfileRequest(name=name.strip(), birth_date=bd, birth_time=(birth_time.strip() or None),
                         birth_place=(birth_place.strip() or None), main_request=event_desc.strip(),
                         analysis_type=AnalysisType.event)
    pid = uuid.uuid4().hex
    background_tasks.add_task(_build_event_and_save, pid, req, ev, event_desc.strip())
    return _spinner_page(pid, "Оцениваю дату…",
                         "Считаю, что эта дата включает у тебя, и собираю вывод. До минуты.")


# ---------------- результат ----------------
def _render_sections(full: str) -> tuple[str, str]:
    """Markdown отчёта → (оглавление, карточки-секции по ##)."""
    parts = re.split(r"(?m)^## ", full or "")
    toc, cards = [], []
    for i, part in enumerate(parts[1:]):
        title = part.split("\n", 1)[0].strip()
        body_md = part[len(title):]
        anchor = f"s{i}"
        toc.append(f"<a href='#{anchor}'>{html.escape(title)}</a>")
        cards.append(f"<section id='{anchor}' class=sec><h2>{html.escape(title)}</h2>{_md_to_html(body_md)}</section>")
    toc_html = ("<div class=toc>" + "".join(toc) + "</div>") if toc else ""
    return toc_html, "".join(cards) or f"<div class=sec>{_md_to_html(full)}</div>"


@router.get("/r/{pid}", response_class=HTMLResponse)
def result(pid: str) -> str:
    data = database.get_profile(pid)
    if data is None:
        st = _web_status.get(pid, "")
        if st.startswith("error:"):
            _web_status.pop(pid, None)
            return _page("Ошибка", f"{_nav()}<div class=wrap><div class=hero><h1>Не получилось собрать разбор</h1>"
                         f"<p class=note>{html.escape(st[6:][:160])}</p><a class=cta href='/'>← попробовать снова</a>"
                         "</div></div>")
        return _spinner_page(pid, "Ещё считаю…", "Почти готово — страница обновится сама.")

    ui = data.get("user_input") or {}
    rep = data.get("report") or {}
    label = _ANALYSIS_LABELS.get(ui.get("analysis_type") or "", "")
    title = html.escape(ui.get("name") or "Профиль") + (f" · {html.escape(label)}" if label else "")
    summary = html.escape((rep.get("short_summary") or "").strip())
    toc_html, cards = _render_sections(rep.get("full_report") or "")
    tech = rep.get("tech_methods") or ""
    advanced = (f"<details><summary>🔬 Полный технический расчёт</summary>"
                f"{_md_to_html(tech)}</details>") if tech else ""
    positions = _positions_html(data)
    body = f"""{_nav()}
    <div class=wrap>
      <div class=hero><h1>{title}</h1></div>
      <div class=cred>{html.escape(CREDIBILITY)}</div>
      {f'<p class=summary>{summary}</p>' if summary else ''}
      <img class=chart src='/chart/{pid}.png' alt='карта профиля' loading=lazy>
      {positions}
      <div class=actions>
        <a class=btnlink href='/voice/{pid}.mp3'>🔊 Слушать разбор</a>
        <a class=btnlink href='/'>＋ Новый разбор</a>
        <a class=btnlink href='/compat'>❤️ Совместимость</a>
      </div>
      {toc_html}
      {cards}
      {advanced}
      <p class=foot>Вероятностная карта для саморефлексии. Важные решения о здоровье, деньгах и отношениях вы принимаете сами.</p>
    </div>"""
    return _page("Твой разбор · Матрица", body)


# ---------------- карта и озвучка ----------------
@router.get("/chart/{pid}.png")
def chart(pid: str) -> Response:
    data = database.get_profile(pid)
    if data is None:
        return Response(status_code=404)
    try:
        png = viz.render_chart(data)
    except Exception as e:  # noqa: BLE001
        database.log_error("exception", "web/chart", f"{type(e).__name__}: {e}")
        return Response(status_code=500)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/voice/{pid}.mp3")
async def voice(pid: str) -> Response:
    data = await run_in_threadpool(database.get_profile, pid)
    full = ((data or {}).get("report") or {}).get("full_report") if data else None
    if not full:
        return Response(status_code=404)
    try:
        audio = await tts.synth(full)
    except Exception as e:  # noqa: BLE001
        await run_in_threadpool(database.log_error, "exception", "web/voice", f"{type(e).__name__}: {e}")
        return Response(status_code=500)
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Content-Disposition": "inline; filename=razbor.mp3"})


# ---------------- админка багов ----------------
@router.get("/admin", response_class=HTMLResponse)
def admin(token: str = Query("")) -> str:
    if not config.DIAG_TOKEN or token != config.DIAG_TOKEN:
        return _page("Админка", f"{_nav()}<div class=wrap><div class=hero><h1>Доступ закрыт</h1>"
                     "<p class=note>Добавь ?token=DIAG_TOKEN в адрес.</p></div></div>")
    stats = database.error_stats()
    rows = database.list_errors(limit=150)
    chips = (
        f"<span class=stat>Всего: <b>{stats.get('total', 0)}</b></span>"
        f"<span class=stat>Не разобрано: <b>{stats.get('unresolved', 0)}</b></span>"
        f"<span class=stat>Сбои AI: <b>{stats.get('ai_failure', 0)}</b></span>"
        f"<span class=stat>Исключения: <b>{stats.get('exception', 0)}</b></span>"
        f"<span class=stat>Жаргон: <b>{stats.get('lang_leak', 0)}</b></span>"
    )
    trs = []
    for r in rows:
        kind = html.escape(r.get("kind") or "")
        when = html.escape((r.get("created_at") or "")[:19].replace("T", " "))
        trs.append(f"<tr><td>{when}</td><td><span class='kind {kind}'>{kind}</span></td>"
                   f"<td>{html.escape(r.get('where_') or '')}</td>"
                   f"<td>{html.escape((r.get('message') or '')[:300])}</td>"
                   f"<td>{r.get('telegram_id') or ''}</td></tr>")
    table = ("<table><tr><th>Когда</th><th>Тип</th><th>Где</th><th>Сообщение</th><th>TG</th></tr>"
             + ("".join(trs) or "<tr><td colspan=5 class=note>Пока чисто — ошибок нет.</td></tr>") + "</table>")
    body = f"{_nav()}<div class=wrap><div class=hero><h1>Админка · баги</h1></div><div>{chips}</div>{table}<p class=foot>Последние 150 записей.</p></div>"
    return _page("Админка", body)
