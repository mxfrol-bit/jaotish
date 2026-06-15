"""Веб-сервис Matrix Engine: лендинг, все типы разборов, страница результата
с оглавлением/картой/озвучкой, админка багов. HTML — строками (без шаблонизатора).

Тяжёлые разборы считаются в фоне (BackgroundTasks) под заранее выданным pid,
страница со спиннером опрашивает /r/{pid} — так HTTP-запрос всегда короткий.
"""
from __future__ import annotations

import html
import json as _json
import re
import uuid
from datetime import date
from pathlib import Path

# Премиальный лендинг, собранный скиллом web-artifacts-builder (React+Tailwind → один HTML).
_LANDING_FILE = Path(__file__).resolve().parents[1] / "landing.html"
_LANDING_HTML = _LANDING_FILE.read_text(encoding="utf-8") if _LANDING_FILE.exists() else ""

# Инвесторская презентация (24 секции), приведена к чёрно-белому из премиум-референса.
_DECK_FILE = Path(__file__).resolve().parents[1] / "deck.html"
_DECK_HTML = _DECK_FILE.read_text(encoding="utf-8") if _DECK_FILE.exists() else ""

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


_SIGNS_RU = ["Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
             "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"]


def _chart_json(data: dict) -> dict | None:
    """Данные натальной карты для живого JS-рендера (реальные долготы)."""
    w = (_modules_of(data).get("western_astrology") or {})
    if w.get("calculation_status") != "calculated":
        return None

    def lon(sign: str, deg) -> float | None:
        try:
            return _SIGNS_RU.index(sign) * 30.0 + float(deg)
        except (ValueError, TypeError):
            return None

    pos = []
    for p in w.get("positions") or []:
        lo = lon(p["sign"], p["degree"])
        if lo is not None:
            pos.append({"k": p["key"], "lon": round(lo, 2),
                        "deg": int(float(p["degree"])), "retro": bool(p.get("retrograde"))})
    asc = w.get("ascendant") or {}
    return {
        "positions": pos,
        "asc": lon(asc.get("sign"), asc.get("degree")) if asc.get("sign") else None,
        "sun": (w.get("sun") or {}).get("sign", ""),
        "moon": (w.get("moon") or {}).get("sign", ""),
        "ascSign": asc.get("sign", ""),
    }


# Живая натальная карта: колесо рисуется, глифы-планеты влетают на реальные позиции (Canvas).
_NATAL_JS = r"""
(function(){
  var cv=document.getElementById('natal'); if(!cv||!cv.getContext||!window.CHART) return;
  var ctx=cv.getContext('2d'), C=window.CHART, W,H,DPR,cx,cy,R,disp,t0=null;
  var SG=['♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓'];
  var PG={sun:'☉',moon:'☽',mercury:'☿',venus:'♀',mars:'♂',jupiter:'♃',saturn:'♄',uranus:'♅',neptune:'♆',pluto:'♇'};
  var INK='#ece7d8',HAIR='rgba(255,255,255,.16)',MUT='rgba(255,255,255,.42)';
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  function xy(lon,r){var a=lon*Math.PI/180;return [cx+r*Math.sin(a), cy-r*Math.cos(a)];}
  function spread(items){
    var arr=items.map(function(p,i){return {i:i,lon:p.lon};}).sort(function(a,b){return a.lon-b.lon;});
    for(var pass=0;pass<60;pass++){var moved=false;
      for(var k=0;k<arr.length;k++){var j=(k+1)%arr.length;var gap=((arr[j].lon-arr[k].lon)%360+360)%360;
        if(arr.length>1&&gap<10){var s=(10-gap)/2;arr[k].lon-=s;arr[j].lon+=s;moved=true;}}
      if(!moved)break;}
    var out=[];arr.forEach(function(o){out[o.i]=o.lon;});return out;
  }
  function resize(){
    DPR=Math.min(window.devicePixelRatio||1,2);
    W=cv.clientWidth;H=W;cv.style.height=W+'px';cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
    cx=W/2;cy=H/2;R=W*0.375;disp=spread(C.positions);
  }
  function ease(x){return 1-Math.pow(1-x,3);}
  function draw(ts){
    if(t0==null)t0=ts;var T=ts-t0;
    ctx.clearRect(0,0,W,H);
    var rIn=R*0.82,rPl=R*0.60;
    var ringP=reduce?1:ease(Math.min(1,T/650));
    ctx.strokeStyle=HAIR;ctx.lineWidth=1.2;
    ctx.beginPath();ctx.arc(cx,cy,R,-Math.PI/2,-Math.PI/2+ringP*6.2832);ctx.stroke();
    ctx.lineWidth=0.8;ctx.beginPath();ctx.arc(cx,cy,rIn,0,6.2832);ctx.stroke();
    for(var d=0;d<360;d+=5){var q0=xy(d,R),q1=xy(d,R-(d%30===0?R*0.05:R*0.024));
      ctx.beginPath();ctx.moveTo(q0[0],q0[1]);ctx.lineTo(q1[0],q1[1]);ctx.stroke();}
    ctx.textAlign='center';ctx.textBaseline='middle';
    for(var i=0;i<12;i++){var a0=xy(i*30,rIn),a1=xy(i*30,R);
      ctx.strokeStyle=HAIR;ctx.beginPath();ctx.moveTo(a0[0],a0[1]);ctx.lineTo(a1[0],a1[1]);ctx.stroke();
      var g=xy(i*30+15,R*1.12);ctx.fillStyle=MUT;ctx.font=(W*0.044)+'px serif';ctx.fillText(SG[i],g[0],g[1]);}
    for(var i=0;i<C.positions.length;i++){var p=C.positions[i];var dl=disp[i];
      var st=reduce?0:300+i*90;var pr=reduce?1:ease(Math.min(1,Math.max(0,(T-st)/820)));
      if(pr<=0)continue;
      var tg=xy(dl,rPl),s0=xy(dl,R*1.32);
      var x=s0[0]+(tg[0]-s0[0])*pr,y=s0[1]+(tg[1]-s0[1])*pr;
      if(pr>0.55){var ta=xy(p.lon,rIn),tb=xy(p.lon,rIn-R*0.035);ctx.strokeStyle=INK;ctx.lineWidth=1;
        ctx.beginPath();ctx.moveTo(ta[0],ta[1]);ctx.lineTo(tb[0],tb[1]);ctx.stroke();}
      ctx.globalAlpha=pr;ctx.fillStyle=INK;ctx.font=(W*0.050)+'px serif';ctx.fillText(PG[p.k]||'·',x,y);
      var dg=xy(dl,rPl-R*0.155);ctx.fillStyle=MUT;ctx.font=(W*0.024)+'px monospace';
      ctx.fillText(p.deg+'°'+(p.retro?' R':''),dg[0],dg[1]);ctx.globalAlpha=1;}
    if(C.asc!=null){var ap=reduce?1:ease(Math.min(1,Math.max(0,(T-1000)/600)));
      if(ap>0){var i0=xy(C.asc,rIn),i1=xy(C.asc,R);ctx.strokeStyle=INK;ctx.lineWidth=2*ap;
        ctx.beginPath();ctx.moveTo(i0[0],i0[1]);ctx.lineTo(i1[0],i1[1]);ctx.stroke();
        var la=xy(C.asc,R*1.12);ctx.fillStyle=INK;ctx.font='bold '+(W*0.026)+'px sans-serif';ctx.fillText('Asc',la[0],la[1]);}}
    if(!reduce&&T<2700)requestAnimationFrame(draw);
  }
  function go(){resize();t0=null;requestAnimationFrame(draw);}
  window.addEventListener('resize',function(){go();});
  go();
})();
"""


def _natal_block(data: dict, pid: str) -> str:
    """Живая Canvas-карта (если есть астрология), иначе — статичный PNG-фолбэк."""
    cj = _chart_json(data)
    if not cj or not cj["positions"]:
        return f"<img class=chart src='/chart/{pid}.png' alt='карта профиля' loading=lazy>"
    foot = f"☉ {cj['sun']}    ☽ {cj['moon']}    Asc {cj['ascSign']}"
    return (
        "<div class=natalwrap><div class=natalcap>Натальная карта — положение светил</div>"
        "<canvas id=natal></canvas>"
        f"<div class=natalfoot>{html.escape(foot)}</div></div>"
        f"<script>window.CHART={_json.dumps(cj, ensure_ascii=False)};</script>"
        f"<script>{_NATAL_JS}</script>"
    )


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
:root{--ink:#ece7d8;--muted:#8f8f8a;--line:rgba(255,255,255,.12);--bg:#08090c;
 --soft:rgba(255,255,255,.035);--accent:#ece7d8;--accent2:#ece7d8;
 --ease:cubic-bezier(.22,.61,.36,1);}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:'Space Grotesk',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
 line-height:1.62;font-size:17px;-webkit-font-smoothing:antialiased;}
h1,h2,.brand{font-family:'Fraunces',Georgia,'Times New Roman',serif;}
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
.cta{display:inline-block;background:var(--accent2);color:#0a0a0b;padding:14px 26px;border-radius:0;
 font-weight:600;font-size:14px;text-transform:uppercase;letter-spacing:.1em;}
.cta:hover{background:#fff;text-decoration:none;}
.cta.ghost{background:transparent;color:var(--ink);border:1px solid var(--line);margin-left:10px;}
.strip{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13.5px;
 padding:18px 0 8px;border-top:1px solid var(--line);margin-top:30px;}
.strip b{color:var(--ink);font-weight:600;}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin:26px 0;}
.feat{border:1px solid var(--line);border-radius:0;padding:20px;background:var(--soft);}
.feat .ic{font-size:24px;}.feat h3{font-size:17px;margin:8px 0 5px;}
.feat p{color:var(--muted);font-size:14.5px;margin:0;}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:22px 0;}
.step{padding:18px 20px;border-radius:14px;background:var(--soft);}
.step .n{display:inline-flex;width:28px;height:28px;align-items:center;justify-content:center;
 background:var(--accent2);color:#0a0a0b;border-radius:50%;font-size:14px;font-weight:700;margin-bottom:8px;}
.step h3{font-size:16px;margin:4px 0 4px;}.step p{color:var(--muted);font-size:14px;margin:0;}
.sectionhead{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
 margin:48px 0 6px;font-weight:650;}
.formcard{border:1px solid var(--line);border-radius:16px;padding:26px;margin:14px 0 10px;}
label{display:block;font-size:14px;color:var(--muted);margin:14px 0 6px;}
input,select{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:0;
 font-size:16px;background:#121214;color:var(--ink);}
input:focus,select:focus{outline:none;border-color:var(--accent);}
input[type=date]::-webkit-calendar-picker-indicator{filter:invert(1);opacity:.5;}
.row{display:flex;gap:14px;flex-wrap:wrap;}.row>div{flex:1;min-width:160px;}
button{margin-top:22px;width:100%;padding:15px 18px;border:0;border-radius:0;
 background:var(--accent2);color:#0a0a0b;font-size:14px;font-weight:600;cursor:pointer;
 text-transform:uppercase;letter-spacing:.1em;}
button:hover{background:#fff;}
.note{color:var(--muted);font-size:13px;margin-top:10px;}
.tabbar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0;}
.tab{padding:9px 15px;border:1px solid var(--line);border-radius:0;font-size:14px;color:var(--ink);}
.tab.on{background:var(--accent2);color:#0a0a0b;border-color:var(--accent2);}
.tab:hover{text-decoration:none;}
.cred{background:var(--soft);border:1px solid var(--line);border-radius:13px;padding:16px 18px;
 color:var(--muted);font-size:14.5px;margin:18px 0;}
.summary{font-size:19px;line-height:1.6;margin:8px 0 22px;}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 26px;}
.toc a{font-size:13.5px;padding:6px 12px;border:1px solid var(--line);border-radius:999px;color:var(--ink);}
.sec{border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin:16px 0;scroll-margin-top:18px;}
.sec h2{font-size:20px;font-weight:680;margin:0 0 8px;letter-spacing:-.01em;}
.sec p{margin:9px 0;}
.chart{display:block;max-width:100%;border:1px solid var(--line);border-radius:0;margin:16px 0;
 filter:invert(1);}
.natalwrap{max-width:640px;margin:22px auto 8px;}
#natal{width:100%;display:block;}
.natalcap{text-align:center;font-family:'Space Mono',ui-monospace,monospace;color:var(--muted);
 font-size:12px;letter-spacing:.18em;text-transform:uppercase;margin-bottom:6px;}
.natalfoot{text-align:center;font-family:'Space Mono',ui-monospace,monospace;color:var(--ink);
 font-size:15px;margin-top:10px;letter-spacing:.04em;}
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


_FONTS = (
    "<link rel=preconnect href='https://fonts.googleapis.com'>"
    "<link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
    "<link rel=stylesheet href='https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&"
    "family=Space+Grotesk:wght@300;400;500;600&"
    "family=Space+Mono:wght@400;700&display=swap'>"
)


# Слой вкуса/движения (design-taste): mono для координат, кастомный ease, transform-only
# микровзаимодействия, scroll-reveal, prefers-reduced-motion.
_POLISH_CSS = (
    ".kicker,.navlinks a,.toc a,.stat,.lstatus,.plpos,.house,.strip,.foot,.sectionhead"
    "{font-family:'Space Mono',ui-monospace,'SFMono-Regular',monospace;}"
    "a,button,.cta,.btnlink,.tab{transition:transform .18s var(--ease),background-color .18s var(--ease),"
    "border-color .18s var(--ease),color .18s var(--ease);}"
    ".cta:hover,button:hover,.btnlink:hover{transform:translateY(-1px);}"
    ".cta:active,button:active,.btnlink:active{transform:translateY(0);}"
    ".feat{transition:transform .22s var(--ease),border-color .22s var(--ease);}"
    ".feat:hover{transform:translateY(-2px);border-color:rgba(255,255,255,.24);}"
    ".pl{transition:transform .2s var(--ease),border-color .2s var(--ease);}"
    ".pl:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.24);}"
    ".toc a:hover{transform:translateY(-1px);}"
    "a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{"
    "outline:2px solid var(--accent);outline-offset:2px;}"
    ".reveal-init{opacity:0;transform:translateY(16px);}"
    ".reveal-in{opacity:1;transform:none;transition:opacity .6s var(--ease),transform .6s var(--ease);}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important;}"
    "a,button,.cta,.btnlink,.feat,.pl{transition:none!important;transform:none!important;}"
    ".reveal-init{opacity:1!important;transform:none!important;}}"
)

_REVEAL_JS = (
    "<script>(function(){"
    "if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;"
    "if(!('IntersectionObserver' in window))return;"
    "var els=document.querySelectorAll('.sec,.feat,.step,.pl');if(!els.length)return;"
    "els.forEach(function(el,i){el.classList.add('reveal-init');el.style.transitionDelay=((i%6)*40)+'ms';});"
    "var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){"
    "e.target.classList.add('reveal-in');e.target.classList.remove('reveal-init');io.unobserve(e.target);}});},"
    "{rootMargin:'0px 0px -7% 0px',threshold:0.08});"
    "els.forEach(function(el){io.observe(el);});})();</script>"
)


def _page(title: str, body: str, head_extra: str = "") -> str:
    return (
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{_FONTS}"
        f"<style>{_CSS}{_HERO_CSS}{_POLISH_CSS}</style>{head_extra}</head>"
        f"<body>{body}{_REVEAL_JS}</body></html>"
    )


def _nav() -> str:
    return (
        "<div class=wrap><div class=nav><a class=brand href='/'>Матрица<span>.</span></a>"
        "<div class=navlinks><a href='/compat'>Совместимость</a>"
        "<a href='/event'>Сделка</a><a href='/proof'>Точность</a>"
        "<a href='/about'>Как это работает</a></div></div></div>"
    )


# Кинематографичная тёмная шапка с живым звёздным полем — единый вау-стиль для всех страниц.
_HERO_CSS = (
    ".chero{position:relative;overflow:hidden;border-bottom:1px solid var(--line);"
    "min-height:44vh;display:flex;align-items:flex-end;}"
    ".chero canvas{position:absolute;inset:0;width:100%;height:100%;}"
    ".chero .inner{position:relative;z-index:2;max-width:880px;margin:0 auto;width:100%;padding:0 22px 46px;}"
    ".chero .kicker{font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--muted);margin:0 0 14px;}"
    ".chero h1{font-size:clamp(30px,6.4vw,60px);margin:0;line-height:1.0;}"
    ".chero .sub{color:var(--muted);font-size:clamp(15px,2vw,18px);margin:14px 0 0;max-width:560px;}"
)

_STARFIELD_JS = r"""
(function(){
  var cv=document.getElementById('sky'); if(!cv||!cv.getContext) return;
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var ctx=cv.getContext('2d'),W,H,DPR,pts=[],mx=-1e5,my=-1e5;
  function build(){
    DPR=Math.min(window.devicePixelRatio||1,2);W=cv.clientWidth;H=cv.clientHeight;
    cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
    var n=Math.round(W*H/6500);pts=[];
    for(var i=0;i<n;i++)pts.push({x:Math.random()*W,y:Math.random()*H,
      vx:(Math.random()-.5)*.14,vy:(Math.random()-.5)*.14,r:Math.random()*1.3+.3,a:Math.random()*.5+.18});
  }
  function frame(){
    ctx.clearRect(0,0,W,H);ctx.fillStyle='#ece7d8';
    for(var i=0;i<pts.length;i++){var p=pts[i];p.x+=p.vx;p.y+=p.vy;
      if(p.x<0)p.x+=W;if(p.x>W)p.x-=W;if(p.y<0)p.y+=H;if(p.y>H)p.y-=H;
      var dx=p.x-mx,dy=p.y-my,d2=dx*dx+dy*dy,rr=p.r,al=p.a;
      if(d2<16000){var k=(16000-d2)/16000;rr+=k*1.8;al+=k*.4;}
      ctx.globalAlpha=al;ctx.beginPath();ctx.arc(p.x,p.y,rr,0,6.2832);ctx.fill();}
    ctx.globalAlpha=1;requestAnimationFrame(frame);
  }
  cv.addEventListener('mousemove',function(e){var r=cv.getBoundingClientRect();mx=e.clientX-r.left;my=e.clientY-r.top;});
  cv.addEventListener('mouseleave',function(){mx=-1e5;my=-1e5;});
  window.addEventListener('resize',build);build();requestAnimationFrame(frame);
})();
"""


def _hero(title: str, kicker: str = "", sub: str = "") -> str:
    """Тёмная шапка с анимированным звёздным полем + крупным засечным заголовком."""
    k = f"<p class=kicker>{html.escape(kicker)}</p>" if kicker else ""
    s = f"<p class=sub>{html.escape(sub)}</p>" if sub else ""
    return (
        "<section class=chero><canvas id=sky></canvas>"
        f"<div class=inner>{k}<h1>{title}</h1>{s}</div></section>"
        "<script>" + _STARFIELD_JS + "</script>"
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
    """Экран ожидания = вау-фон: из точек собирается глобус-планета, статус без смайликов."""
    import json as _json
    # убираем эмодзи из статусов — оставляем чистый текст
    clean = [re.sub(r"^[^0-9A-Za-zА-Яа-яЁё]+", "", m).strip() for m in LOADING_MESSAGES]
    msgs = _json.dumps(clean, ensure_ascii=False)
    css = (
        "#sky{position:fixed;inset:0;width:100%;height:100%;cursor:crosshair;}"
        ".lbrand{position:fixed;top:22px;left:24px;z-index:3;font-family:Fraunces,serif;"
        "font-size:18px;color:#ece7d8;text-decoration:none;}"
        ".loverlay{position:fixed;left:0;right:0;bottom:0;z-index:2;pointer-events:none;"
        "padding:0 24px 13vh;text-align:center;}"
        ".ltitle{font-family:Fraunces,serif;font-weight:500;font-size:clamp(24px,4.6vw,44px);"
        "color:#ece7d8;margin:0 0 18px;letter-spacing:-.01em;}"
        ".lstatus{font-size:12px;letter-spacing:.24em;text-transform:uppercase;color:#8f8f8a;"
        "min-height:1.4em;transition:opacity .4s;}"
    )
    body = (
        "<canvas id=sky></canvas>"
        "<a class=lbrand href='/'>Матрица</a>"
        "<div class=loverlay><h1 class=ltitle>" + html.escape(title) + "</h1>"
        "<div id=ld class=lstatus></div></div>"
        "<script>" + _STAGE_JS + "</script>"
        "<script>(function(){var M=" + msgs + ",i=Math.floor(Math.random()*M.length),"
        "e=document.getElementById('ld');function t(){e.style.opacity=0;"
        "setTimeout(function(){e.textContent=M[i%M.length];i++;e.style.opacity=1;},260);}"
        "t();setInterval(t,1900);})();</script>"
    )
    # globe успевает собраться до перезагрузки, которая опрашивает результат.
    head = "<meta http-equiv='refresh' content='8;url=/r/" + pid + "'><style>" + css + "</style>"
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
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches) return;
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
    # Премиальный лендинг (React+Tailwind, собран в один HTML). Форма постит на /report.
    if _LANDING_HTML:
        return _LANDING_HTML
    return _page("Матрица", f"{_nav()}<div class=wrap><div class=hero><h1>Матрица</h1>"
                 "<a class=cta href='/proof'>Узнать больше</a></div></div>")


@router.get("/deck", response_class=HTMLResponse)
@router.get("/pitch", response_class=HTMLResponse)
def deck() -> str:
    # Инвесторская презентация (чёрно-белая). Самодостаточный HTML.
    if _DECK_HTML:
        return _DECK_HTML
    return _page("Презентация", f"{_nav()}<div class=wrap><div class=hero><h1>Презентация недоступна</h1></div></div>")


@router.get("/about", response_class=HTMLResponse)
def about() -> str:
    body = (f"{_nav()}{_hero('Как это работает', 'Метод', 'На стыке точного расчёта и поведенческой психологии.')}"
            f"<div class=wrap><div class=sec>{_md_to_html(METHOD_BASIS)}</div>"
            "<p class=foot><a href='/proof'>Доказательство точности →</a> · "
            "<a href='/#form'>к разбору</a></p></div>")
    return _page("Как это работает · Матрица", body)


@router.get("/proof", response_class=HTMLResponse)
def proof() -> str:
    """Маркетинговый экран: откуда берётся точность + живой расчёт-образец."""
    geo = {"lat": 51.4769, "lon": 0.0, "timezone": "UTC"}  # Гринвич, эпоха J2000
    sample = {"calculation_modules": {"western_astrology": astrology.western(date(2000, 1, 1), "12:00", geo)}}
    table = _positions_html(sample)
    body = f"""{_nav()}{_hero('Откуда точность', 'Доказательство', 'Те же эфемериды, что в профессиональной астрономии и навигации аппаратов.')}
    <div class=wrap>
      <div class=strip>
        <span><b>NASA JPL DE431</b></span>
        <span><b>Детерминированно</b> — одна дата всегда даёт один результат</span>
        <span><b>Точность до угловой минуты</b></span>
      </div>
      <div class=sec>
        <p>Положения светил считает <b>Swiss Ephemeris</b> (Astrodienst) на базе
        <b>NASA JPL DE431</b> — тех же эфемерид, что используют в профессиональной астрономии
        и расчёте траекторий космических аппаратов.</p>
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
    body = f"""{_nav()}{_hero('Совместимость', 'Резонанс двух кодов', 'Где вы усиливаете и где триггерите друг друга — без приговора «вместе/нет».')}
    <div class=wrap id=form>
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
    body = f"""{_nav()}{_hero('Сделка / событие', 'Электив на дату', 'Стоит ли входить в конкретную дату: что она включает у тебя.')}
    <div class=wrap id=form>
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


# ---------------- дашборд личности (живой профиль из расчёта) ----------------
_LP_WORD = {1: "Лидер", 2: "Дипломат", 3: "Творец", 4: "Архитектор", 5: "Искатель",
            6: "Хранитель", 7: "Аналитик", 8: "Стратег", 9: "Наставник",
            11: "Визионер", 22: "Мастер", 33: "Учитель"}
_EL_WORD = {"огонь": "Двигатель", "земля": "Строитель", "воздух": "Связной", "вода": "Эмпат"}
_EL_LOVE = {"огонь": "страсть и темп", "земля": "надёжность и быт", "воздух": "свобода и разговор", "вода": "глубина больше флирта"}
_DIG_EL = {1: "огонь", 9: "огонь", 5: "воздух", 3: "воздух", 2: "вода", 7: "вода",
           4: "земля", 8: "земля", 6: "земля", 11: "воздух", 22: "земля", 33: "вода"}


def _elements_balance(mods: dict) -> dict:
    w = mods.get("western_astrology") or {}
    eb = w.get("elements_balance")
    if eb and w.get("calculation_status") == "calculated":
        return {k: int(eb.get(k, 0)) for k in ("огонь", "земля", "воздух", "вода")}
    num = mods.get("numerology") or {}
    bal = {"огонь": 0, "земля": 0, "воздух": 0, "вода": 0}
    for k in ("life_path", "destiny_number", "soul_number", "personality_number", "birthday_number"):
        v = num.get(k)
        if isinstance(v, int):
            bal[_DIG_EL.get(v, "воздух")] += 1
    if sum(bal.values()) == 0:
        bal = {"огонь": 1, "земля": 1, "воздух": 1, "вода": 1}
    return bal


def _profile_metrics(data: dict) -> dict:
    """Детерминированно выводим архетип, целостность и 6 шкал из посчитанного баланса стихий."""
    mods = _modules_of(data)
    bal = _elements_balance(mods)
    tot = sum(bal.values()) or 1
    f, e, a, w = (bal["огонь"] / tot, bal["земля"] / tot, bal["воздух"] / tot, bal["вода"] / tot)

    def sc(x: float) -> int:
        return max(18, min(97, round(30 + x * 170)))

    scores = [
        ("Энергия действия", sc(f * 0.8 + a * 0.2)),
        ("Стратегичность", sc(e * 0.55 + a * 0.45)),
        ("Эмоциональная глубина", sc(w * 0.8 + e * 0.2)),
        ("Денежная устойчивость", sc(e * 0.7 + f * 0.3)),
        ("Коммуникация", sc(a * 0.7 + f * 0.3)),
        ("Интуиция", sc(w * 0.65 + a * 0.35)),
    ]
    mean = 0.25
    sd = (sum((x - mean) ** 2 for x in (f, e, a, w)) / 4) ** 0.5
    wholeness = max(45, min(96, round(94 - sd * 180)))

    num = mods.get("numerology") or {}
    lp = num.get("life_path")
    dom = max(bal, key=bal.get)
    archetype = f"{_LP_WORD.get(lp, 'Искатель')} · {_EL_WORD.get(dom, 'Связной')}"
    arc = (mods.get("arcana_22") or {}).get("core_arcana") or {}
    py = num.get("personal_year")
    tiles = [
        ("Ядро", arc.get("name") or _LP_WORD.get(lp, "—")),
        ("Любовь", _EL_LOVE.get(dom, "—")),
        ("Реализация", _LP_WORD.get(lp, "—") + "-роль"),
        ("Период", f"личный год {py}" if py else "—"),
    ]
    return {"archetype": archetype, "wholeness": wholeness, "scores": scores, "tiles": tiles, "dom": dom}


_DASH_CSS = """
:root{--bg:#070708;--ink:#f6f6f6;--ink2:#cfcfcf;--mut:#8a8a8a;--line:rgba(255,255,255,.12);
 --soft:rgba(255,255,255,.045);--grad:linear-gradient(120deg,#5b8cff,#9d7bff 55%,#caa6ff);
 --ease:cubic-bezier(.22,.61,.36,1);
 --disp:'Bricolage Grotesque','Space Grotesk',system-ui,sans-serif;--mono:'JetBrains Mono',ui-monospace,monospace;}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink2);font-family:'Inter',system-ui,sans-serif;line-height:1.6;
 -webkit-font-smoothing:antialiased}
.glow{position:fixed;border-radius:50%;filter:blur(100px);opacity:.5;pointer-events:none;z-index:0}
.glow.a{width:560px;height:560px;top:-160px;right:-120px;background:radial-gradient(circle,#5b8cff3d,transparent 70%)}
.glow.b{width:520px;height:520px;bottom:-180px;left:-120px;background:radial-gradient(circle,#9d7bff33,transparent 70%)}
.wrap{position:relative;z-index:2;max-width:1000px;margin:0 auto;padding:30px 22px 90px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:40px}
.brand{font-family:var(--disp);font-weight:800;font-size:18px;color:var(--ink);letter-spacing:.02em}
.top a{color:var(--mut);font-family:var(--mono);font-size:12px;text-transform:uppercase;letter-spacing:.12em;text-decoration:none}
.top a:hover{color:var(--ink)}
.kick{font-family:var(--mono);font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#a78bfa;margin-bottom:14px}
.head{display:flex;flex-wrap:wrap;gap:26px 40px;align-items:flex-end;justify-content:space-between;
 border-bottom:1px solid var(--line);padding-bottom:30px;margin-bottom:34px}
.name{font-family:var(--disp);font-weight:800;font-size:clamp(34px,6vw,64px);line-height:.98;color:var(--ink);letter-spacing:-.02em}
.arche{font-family:var(--mono);font-size:14px;letter-spacing:.06em;color:var(--ink2);margin-top:12px}
.ring{text-align:center;flex:none}
.ring .num{font-family:var(--disp);font-weight:800;font-size:54px;
 background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;line-height:1}
.ring .lbl{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--mut);margin-top:4px}
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px 40px;margin:6px 0 40px}
.bar .row{display:flex;justify-content:space-between;font-size:14px;margin-bottom:8px}
.bar .row .v{font-family:var(--mono);color:var(--ink)}
.track{height:6px;background:var(--soft);border:1px solid var(--line);border-radius:999px;overflow:hidden}
.fill{height:100%;width:0;background:var(--grad);border-radius:999px;transition:width 1.1s var(--ease)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:42px}
.tile{border:1px solid var(--line);border-radius:18px;padding:18px 20px;background:var(--soft)}
.tile .t{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:var(--mut)}
.tile .d{font-family:var(--disp);font-size:19px;color:var(--ink);margin-top:8px;letter-spacing:-.01em}
.acts{display:flex;flex-wrap:wrap;gap:12px}
.acts a{font-family:var(--mono);font-size:12px;text-transform:uppercase;letter-spacing:.1em;text-decoration:none;
 padding:13px 20px;border-radius:999px;border:1px solid var(--line);color:var(--ink2);transition:transform .2s var(--ease),border-color .2s var(--ease)}
.acts a:hover{transform:translateY(-2px);border-color:rgba(255,255,255,.4);color:var(--ink)}
.acts a.go{background:var(--ink);color:#070708;border-color:var(--ink)}
.foot{color:var(--mut);font-size:12px;font-family:var(--mono);margin-top:40px;letter-spacing:.04em}
@media (prefers-reduced-motion:reduce){.fill{transition:none}.acts a{transition:none}}
"""


@router.get("/profile/{pid}", response_class=HTMLResponse)
def dashboard(pid: str) -> str:
    data = database.get_profile(pid)
    if data is None:
        return _spinner_page(pid, "Собираю профиль…", "Почти готово — страница обновится сама.")
    ui = data.get("user_input") or {}
    m = _profile_metrics(data)
    name = html.escape(ui.get("name") or "Профиль")
    bars = "".join(
        f"<div class=bar><div class=row><span>{html.escape(lbl)}</span><span class=v>{val}</span></div>"
        f"<div class=track><div class=fill data-v='{val}'></div></div></div>"
        for lbl, val in m["scores"]
    )
    tiles = "".join(
        f"<div class=tile><div class=t>{html.escape(t)}</div><div class=d>{html.escape(str(d))}</div></div>"
        for t, d in m["tiles"]
    )
    fonts = (
        "<link rel=preconnect href='https://fonts.googleapis.com'>"
        "<link rel=stylesheet href='https://fonts.googleapis.com/css2?"
        "family=Bricolage+Grotesque:opsz,wght@12..96,400..800&family=Inter:wght@400;500;600&"
        "family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@500;700&display=swap'>"
    )
    body = f"""<div class=glow a></div><div class=glow b></div><div class=wrap>
      <div class=top><span class=brand>Матрица</span>
        <a href='/r/{pid}'>полный разбор →</a></div>
      <div class=kick>Профиль личности</div>
      <div class=head>
        <div><div class=name>{name}</div><div class=arche>Архетип · {html.escape(m['archetype'])}</div></div>
        <div class=ring><div class=num>{m['wholeness']}</div><div class=lbl>Целостность</div></div>
      </div>
      <div class=bars>{bars}</div>
      <div class=tiles>{tiles}</div>
      <div class=acts>
        <a class=go href='/r/{pid}'>Открыть разбор</a>
        <a href='/chart/{pid}.png' target=_blank>Карта неба</a>
        <a href='/voice/{pid}.mp3'>Слушать</a>
        <a href='/compat'>Совместимость</a>
      </div>
      <p class=foot>Шкалы выведены из рассчитанного баланса стихий твоей карты — детерминированно, не случайно.</p>
    </div>
    <script>window.addEventListener('load',function(){{requestAnimationFrame(function(){{
      document.querySelectorAll('.fill').forEach(function(f){{f.style.width=f.dataset.v+'%';}});}});}});</script>"""
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{name} · Профиль · Матрица</title>{fonts}<style>{_DASH_CSS}</style></head>"
            f"<body>{body}</body></html>")


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
    advanced = (f"<details><summary>Полный технический расчёт</summary>"
                f"{_md_to_html(tech)}</details>") if tech else ""
    positions = _positions_html(data)
    body = f"""{_nav()}{_hero(title, 'Твой разбор')}
    <div class=wrap>
      <div class=cred>{html.escape(CREDIBILITY)}</div>
      {f'<p class=summary>{summary}</p>' if summary else ''}
      {_natal_block(data, pid)}
      {positions}
      <div class=actions>
        <a class=btnlink href='/profile/{pid}'>Дашборд профиля</a>
        <a class=btnlink href='/voice/{pid}.mp3'>Слушать разбор</a>
        <a class=btnlink href='/'>Новый разбор</a>
        <a class=btnlink href='/compat'>Совместимость</a>
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
    body = f"{_nav()}{_hero('Админка · баги', 'Мониторинг')}<div class=wrap><div>{chips}</div>{table}<p class=foot>Последние 150 записей.</p></div>"
    return _page("Админка", body)
