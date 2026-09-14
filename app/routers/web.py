"""Веб-сервис Matrix Engine: лендинг, все типы разборов, страница результата
с оглавлением/картой/озвучкой, админка багов. HTML — строками (без шаблонизатора).

Тяжёлые разборы считаются в фоне (BackgroundTasks) под заранее выданным pid,
страница со спиннером опрашивает /r/{pid} — так HTTP-запрос всегда короткий.
"""
from __future__ import annotations

import calendar
import html
import json as _json
import re
import uuid
import time
import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path

# Редактируемый HTML; общие стили и поведение в app/static/.
_LANDING_FILE = Path(__file__).resolve().parents[1] / "landing.html"
_LANDING_HTML = _LANDING_FILE.read_text(encoding="utf-8") if _LANDING_FILE.exists() else ""

# Инвесторская презентация (24 секции), приведена к чёрно-белому из премиум-референса.
_DECK_FILE = Path(__file__).resolve().parents[1] / "deck.html"
_DECK_HTML = _DECK_FILE.read_text(encoding="utf-8") if _DECK_FILE.exists() else ""

from fastapi import APIRouter, BackgroundTasks, Form, Query, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .. import config, database, tts, viz
from ..calc import astrology
from ..engine import build_event, build_profile, build_synastry
from ..models import AnalysisType, ProfileRequest
from .. import synthesis
from ..synthesis import ANALYSIS_TITLES, LOADING_MESSAGES, METHOD_BASIS, date_ru

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
                        "deg": int(float(p["degree"])), "retro": bool(p.get("retrograde")),
                        "name": p.get("name", ""), "sign": p["sign"]})
    asc = w.get("ascendant") or {}
    # аспекты между планетами (облака-связи): соединение/секстиль/квадрат/тригон/оппозиция
    aspd = [(0, 7), (60, 5), (90, 6), (120, 6), (180, 7)]
    aspects = []
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            d = abs(pos[i]["lon"] - pos[j]["lon"]) % 360
            if d > 180:
                d = 360 - d
            for ang, orb in aspd:
                off = abs(d - ang)
                if off <= orb:
                    aspects.append({"a": i, "b": j, "s": round(1 - off / orb, 2)})
                    break
    return {
        "positions": pos,
        "aspects": aspects,
        "moonIdx": next((i for i, p in enumerate(pos) if p["k"] == "moon"), -1),
        "asc": lon(asc.get("sign"), asc.get("degree")) if asc.get("sign") else None,
        "sun": (w.get("sun") or {}).get("sign", ""),
        "moon": (w.get("moon") or {}).get("sign", ""),
        "ascSign": asc.get("sign", ""),
    }


# Живая натальная карта: колесо рисуется, глифы-планеты влетают на реальные позиции (Canvas).
_NATAL_JS = r"""
(function(){
  var cv=document.getElementById('natal'); if(!cv||!cv.getContext||!window.CHART) return;
  var ctx=cv.getContext('2d'), C=window.CHART, W,H,DPR,cx,cy,R,U,t0=null;
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var SG=['♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓'];
  var PG={sun:'☉',moon:'☽',mercury:'☿',venus:'♀',mars:'♂',jupiter:'♃',saturn:'♄',uranus:'♅',neptune:'♆',pluto:'♇'};
  var SIGNC='#cfd3df', PLANETC='#e8c168', GLOW='232,193,104';
  var SYMFONT="'Apple Symbols','Segoe UI Symbol','Noto Sans Symbols2','DejaVu Sans',serif";
  // упрощённые астеризмы 12 зодиакальных созвездий (норм. координаты, y вверх)
  var CON=[
   {p:[[-.8,-.2],[-.2,0],[.3,.1],[.8,.4]],l:[[0,1],[1,2],[2,3]]},
   {p:[[-.9,.9],[-.65,.55],[-.2,0],[0,-.12],[.22,0],[.68,.5],[.92,.8]],l:[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6]]},
   {p:[[-.5,.9],[-.5,.1],[-.45,-.55],[.45,.9],[.45,.1],[.5,-.55]],l:[[0,1],[1,2],[3,4],[4,5],[0,3]]},
   {p:[[0,.65],[0,-.05],[-.55,-.7],[.55,-.7]],l:[[0,1],[1,2],[1,3]]},
   {p:[[-.2,.9],[-.45,.5],[-.32,.1],[-.02,-.06],[.28,.05],[.88,-.45],[.32,-.72]],l:[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,3]]},
   {p:[[-.85,.55],[-.3,.42],[.02,0],[.32,-.35],[.5,-.85],[.12,.66]],l:[[5,1],[1,2],[2,3],[3,4]]},
   {p:[[-.6,.35],[.02,.62],[.62,.2],[.1,-.5]],l:[[0,1],[1,2],[2,3],[3,0]]},
   {p:[[-.92,.5],[-.66,.34],[-.42,.22],[-.12,.02],[.16,-.2],[.42,-.42],[.6,-.66],[.46,-.9],[.2,-.92]],l:[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,7],[7,8]]},
   {p:[[-.42,-.32],[-.52,.22],[0,.44],[.5,.22],[.4,-.32],[-.92,.12],[.86,0],[0,.64]],l:[[0,1],[1,2],[2,3],[3,4],[4,0],[1,5],[3,6],[2,7]]},
   {p:[[-.82,.2],[.02,.5],[.8,0],[.12,-.6],[-.5,-.3]],l:[[0,1],[1,2],[2,3],[3,4],[4,0]]},
   {p:[[-.9,.3],[-.5,-.12],[-.1,.32],[.32,-.12],[.72,.32],[.92,-.18]],l:[[0,1],[1,2],[2,3],[3,4],[4,5]]},
   {p:[[-.9,.6],[-.5,.3],[-.12,.02],[0,-.06],[.5,.2],[.9,.5]],l:[[0,1],[1,2],[2,3],[3,4],[4,5]]}
  ];
  var BGCON=[
   {p:[[-1,.4],[-.6,.3],[-.25,.2],[.05,.05],[.05,-.35],[-.35,-.4],[-.35,0]],l:[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,3]]},
   {p:[[-.5,.7],[.5,.6],[-.6,-.6],[.5,-.7],[-.12,.04],[0,0],[.12,-.04]],l:[[4,5],[5,6],[0,4],[1,6],[2,4],[3,6]]},
   {p:[[-.9,.1],[-.45,.42],[0,0],[.45,.42],[.9,.1]],l:[[0,1],[1,2],[2,3],[3,4]]},
   {p:[[0,.85],[0,-.7],[-.75,.12],[.75,.12],[0,.12]],l:[[0,4],[4,1],[2,4],[4,3]]},
   {p:[[-.85,.55],[-.3,.42],[.02,0],[.32,-.35],[.5,-.85],[.12,.66]],l:[[5,1],[1,2],[2,3],[3,4]]},
   {p:[[-.92,.5],[-.66,.34],[-.42,.22],[-.12,.02],[.16,-.2],[.42,-.42],[.6,-.66],[.46,-.9],[.2,-.92]],l:[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,7],[7,8]]}
  ];
  var off=document.createElement('canvas'), octx=off.getContext('2d');
  function xy(lon,r){var a=lon*Math.PI/180;return [cx+r*Math.sin(a), cy-r*Math.cos(a)];}
  function ease(x){return x<0?0:x>1?1:1-Math.pow(1-x,3);}
  function cl(x){return x<0?0:x>1?1:x;}
  function spread(items){
    var arr=items.map(function(p,i){return {i:i,lon:p.lon};}).sort(function(a,b){return a.lon-b.lon;});
    for(var pass=0;pass<70;pass++){var moved=false;
      for(var k=0;k<arr.length;k++){var j=(k+1)%arr.length;var gap=((arr[j].lon-arr[k].lon)%360+360)%360;
        if(arr.length>1&&gap<16){var s=(16-gap)/2;arr[k].lon-=s;arr[j].lon+=s;moved=true;}}
      if(!moved)break;}
    var o=[];arr.forEach(function(x){o[x.i]=x.lon;});return o;
  }
  var GL=[], PA=[], STAR=[], BG=[], PPOS=[], ascP=null;
  function addGlyph(g,gx,gy,size,color,start,dur){
    var gi=GL.length; GL.push({g:g,gx:gx,gy:gy,size:size,c:color,start:start,dur:dur});
    var s=Math.round(size),pad=Math.round(size*0.3),d=s+pad*2;
    off.width=d;off.height=d;octx.clearRect(0,0,d,d);
    octx.fillStyle='#fff';octx.textAlign='center';octx.textBaseline='middle';octx.font=s+'px '+SYMFONT;
    octx.fillText(g+'︎',d/2,d/2);
    var im=octx.getImageData(0,0,d,d).data,step=Math.max(2,Math.round(size/22));
    for(var y=0;y<d;y+=step)for(var x=0;x<d;x+=step){
      if(im[(y*d+x)*4+3]>120){var a=Math.random()*6.2832,rad=W*(0.28+Math.random()*0.5);
        PA.push({sx:cx+Math.cos(a)*rad,sy:cy+Math.sin(a)*rad,tx:gx+(x-d/2),ty:gy+(y-d/2),gi:gi,c:color,
          dx:(Math.random()-.5)*46,dy:(Math.random()-.5)*46});}
    }
  }
  function build(){
    DPR=Math.min(window.devicePixelRatio||1,2);
    W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);
    cx=W/2;cy=H/2;U=Math.min(W,H);R=U*0.40;
    GL=[];PA=[];STAR=[];PPOS=[];
    var rPl=R*0.58;
    var disp=spread(C.positions);
    var ord=disp.map(function(l,i){return {i:i,l:l};}).sort(function(a,b){return a.l-b.l;});
    var rArr=[],lvl=0;
    for(var k=0;k<ord.length;k++){if(k>0){var gp=((ord[k].l-ord[k-1].l)%360+360)%360;lvl=(gp<20)?(lvl+1)%3:0;}rArr[ord[k].i]=rPl-lvl*rPl*0.16;}
    var psz=U*0.046;
    for(var i=0;i<C.positions.length;i++){var pp=C.positions[i],dl=disp[i],pos=xy(dl,rArr[i]);
      PPOS[i]=pos; addGlyph(PG[pp.k]||'☉',pos[0],pos[1],psz,PLANETC,700+i*80,640);}
    ascP=(C.asc!=null)?{i0:xy(C.asc,R*0.82),i1:xy(C.asc,R),la:xy(C.asc,R*1.27)}:null;
    var n=Math.round(W*H/2200);
    for(var i=0;i<n;i++)STAR.push({x:Math.random()*W,y:Math.random()*H,r:Math.random()*0.85+0.12,
      vx:(Math.random()-.5)*0.05,vy:(Math.random()-.5)*0.05,a:Math.random()*0.2+0.03,ph:Math.random()*6.28});
    BG=[];var nb=Math.max(5,Math.round(W/115));
    for(var b=0;b<nb;b++){var pat=BGCON[(Math.random()*BGCON.length)|0];
      var bx=Math.random()*W,by=Math.random()*H,sc=U*(0.055+Math.random()*0.07),rot=Math.random()*6.2832,co=Math.cos(rot),si=Math.sin(rot);
      var pts=pat.p.map(function(q){var rx=q[0]*co-q[1]*si,ry=q[0]*si+q[1]*co;return [bx+rx*sc,by-ry*sc];});
      BG.push({pts:pts,l:pat.l,ph:Math.random()*6.28,a:0.09+Math.random()*0.10});}
  }
  function stardust(t){
    for(var i=0;i<STAR.length;i++){var s=STAR[i];if(!reduce){s.x+=s.vx;s.y+=s.vy;
      if(s.x<0)s.x+=W;if(s.x>W)s.x-=W;if(s.y<0)s.y+=H;if(s.y>H)s.y-=H;}
      var tw=reduce?1:(0.6+0.4*Math.sin(t*0.001+s.ph));
      ctx.globalAlpha=s.a*tw;ctx.fillStyle='#cdd2e0';ctx.beginPath();ctx.arc(s.x,s.y,s.r,0,6.2832);ctx.fill();}
    ctx.globalAlpha=1;
  }
  function drawBG(t){
    for(var b=0;b<BG.length;b++){var g=BG[b];
      ctx.strokeStyle='rgba(200,206,224,'+(g.a*0.42)+')';ctx.lineWidth=0.6;
      for(var k=0;k<g.l.length;k++){var A=g.pts[g.l[k][0]],B=g.pts[g.l[k][1]];ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);ctx.stroke();}
      for(var k=0;k<g.pts.length;k++){var P=g.pts[k];var tw=reduce?1:(0.5+0.5*Math.sin(t*0.0015+k+g.ph));
        ctx.globalAlpha=g.a*tw;ctx.fillStyle=(k===0?'#dfe3f0':'#c6cbdd');ctx.beginPath();ctx.arc(P[0],P[1],(k===0?1.5:1.05),0,6.2832);ctx.fill();}}
    ctx.globalAlpha=1;
  }
  function drawWheel(al){
    ctx.globalAlpha=al;ctx.strokeStyle='rgba(190,194,210,.45)';ctx.lineWidth=1;
    ctx.beginPath();ctx.arc(cx,cy,R,0,6.2832);ctx.stroke();
    ctx.strokeStyle='rgba(150,154,170,.24)';ctx.lineWidth=0.7;ctx.beginPath();ctx.arc(cx,cy,R*0.82,0,6.2832);ctx.stroke();
    for(var i=0;i<12;i++){var a0=xy(i*30,R*0.82),a1=xy(i*30,R);ctx.beginPath();ctx.moveTo(a0[0],a0[1]);ctx.lineTo(a1[0],a1[1]);ctx.stroke();}
    ctx.globalAlpha=1;
  }
  function drawCon(t,al){
    for(var i=0;i<12;i++){var con=CON[i];if(!con)continue;var c=xy(i*30+15,R*1.075),sc=R*0.105;
      ctx.strokeStyle='rgba(200,205,222,'+(al*0.26)+')';ctx.lineWidth=0.7;
      for(var k=0;k<con.l.length;k++){var A=con.p[con.l[k][0]],B=con.p[con.l[k][1]];
        ctx.beginPath();ctx.moveTo(c[0]+A[0]*sc,c[1]-A[1]*sc);ctx.lineTo(c[0]+B[0]*sc,c[1]-B[1]*sc);ctx.stroke();}
      for(var k=0;k<con.p.length;k++){var P=con.p[k];var tw=0.55+0.45*Math.sin(t*0.0018+k+i*1.3);
        ctx.globalAlpha=al*tw;ctx.fillStyle=(k===0?'#f3edda':'#d2d6e2');
        ctx.beginPath();ctx.arc(c[0]+P[0]*sc,c[1]-P[1]*sc,(k===0?1.9:1.2),0,6.2832);ctx.fill();}
    }
    ctx.globalAlpha=1;
  }
  function drawSignLabels(al){
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.globalAlpha=al;ctx.fillStyle=SIGNC;
    ctx.font=(U*0.028)+'px '+SYMFONT;
    for(var i=0;i<12;i++){var g=xy(i*30+15,R*1.18);ctx.fillText(SG[i]+'︎',g[0],g[1]);}
    ctx.globalAlpha=1;
  }
  function drawAspects(al,t){
    if(!C.aspects)return;ctx.save();ctx.lineCap='round';
    for(var i=0;i<C.aspects.length;i++){var as=C.aspects[i];var A=PPOS[as.a],B=PPOS[as.b];if(!A||!B)continue;
      var moon=(as.a===C.moonIdx||as.b===C.moonIdx);
      ctx.strokeStyle='rgba('+GLOW+','+(al*(moon?0.13:0.05))+')';ctx.lineWidth=moon?0.9:0.55;
      ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);ctx.stroke();}
    ctx.restore();
  }
  function crisp(t){
    ctx.textAlign='center';ctx.textBaseline='middle';
    for(var i=0;i<GL.length;i++){var G=GL[i];var settle=reduce?1:cl((t-(G.start+G.dur))/520);
      if(settle<=0)continue;ctx.globalAlpha=settle;ctx.font=G.size+'px '+SYMFONT;ctx.fillStyle=G.c;
      ctx.shadowColor='rgba('+GLOW+',0.55)';ctx.shadowBlur=8;ctx.fillText(G.g+'︎',G.gx,G.gy);ctx.shadowBlur=0;}
    ctx.globalAlpha=1;
  }
  function frame(ts){
    if(t0==null)t0=ts;var t=ts-t0;ctx.clearRect(0,0,W,H);
    stardust(t);drawBG(t);
    var wf=reduce?1:ease(cl(t/700));
    drawWheel(wf);drawCon(t,reduce?1:ease(cl((t-300)/900)));drawSignLabels(reduce?1:ease(cl((t-500)/700)));
    var cg=ctx.createRadialGradient(cx,cy,0,cx,cy,R*0.55);cg.addColorStop(0,'rgba('+GLOW+','+(0.06*wf)+')');cg.addColorStop(1,'rgba('+GLOW+',0)');ctx.fillStyle=cg;ctx.beginPath();ctx.arc(cx,cy,R*0.55,0,6.2832);ctx.fill();
    drawAspects(reduce?1:ease(cl((t-1700)/900)),t);
    for(var i=0;i<PA.length;i++){var p=PA[i];var G=GL[p.gi];
      var settle=reduce?1:cl((t-(G.start+G.dur))/520);if(settle>=1)continue;
      var ap=reduce?1:ease(cl((t-G.start)/G.dur));var x,y,al;
      if(settle<=0){x=p.sx+(p.tx-p.sx)*ap;y=p.sy+(p.ty-p.sy)*ap;al=ap*0.9;}
      else{x=p.tx+p.dx*settle;y=p.ty+p.dy*settle;al=(1-settle)*0.8;}
      ctx.globalAlpha=al;ctx.fillStyle=p.c;ctx.fillRect(x,y,1.4,1.4);}
    ctx.globalAlpha=1;crisp(t);
    if(ascP){var al=reduce?1:ease(cl((t-1100)/600));if(al>0){ctx.globalAlpha=al;
      ctx.strokeStyle='#ffffff';ctx.lineWidth=1.6;ctx.beginPath();ctx.moveTo(ascP.i0[0],ascP.i0[1]);ctx.lineTo(ascP.i1[0],ascP.i1[1]);ctx.stroke();
      ctx.fillStyle='#fff';ctx.font='bold '+(U*0.024)+'px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('Asc',ascP.la[0],ascP.la[1]);ctx.globalAlpha=1;}}
    if(reduce)return;requestAnimationFrame(frame);
  }
  function go(){build();t0=null;requestAnimationFrame(frame);}
  window.addEventListener('resize',go);go();
})();
"""


def _natal_block(data: dict, pid: str) -> str:
    """Живая Canvas-карта (если есть астрология), иначе — статичный PNG-фолбэк."""
    cj = _chart_json(data)
    if not cj or not cj["positions"]:
        return f"<img class=chart src='/chart/{pid}.png' alt='карта профиля' loading=lazy>"
    foot = f"☉︎ {cj['sun']}    ☽︎ {cj['moon']}    Asc {cj['ascSign']}"
    gly = {"sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
           "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇"}
    legend = "".join(
        f"<div class=lgrow><span class=lgico>{gly.get(p['k'], '·')}︎</span>"
        f"<b>{html.escape(p.get('name') or '')}</b>"
        f"<span class=lgpos>{html.escape(p['sign'])} {p['deg']}°{' ℞' if p['retro'] else ''}</span></div>"
        for p in cj["positions"]
    )
    return (
        "<div class=natalwrap>"
        "<div class=natalcap>Снаружи — знаки зодиака · внутри — твои планеты · золотым</div>"
        "<canvas id=natal></canvas>"
        f"<div class=natalfoot>{html.escape(foot)}</div>"
        f"<div class=natalleg>{legend}</div>"
        "<div class=natalhint>Каждый золотой символ на карте = строка в списке: планета · знак · градус.</div>"
        "</div>"
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

_ANALYSIS_LABELS = ANALYSIS_TITLES

# Общий вопрос темы — то, что веб-форма спрашивает у AI (в боте вопрос выбирают кнопкой).
_GENERAL_QUESTION = {
    "personality": "Общий разбор характера: сильные стороны, привычные реакции, что мешает.",
    "relationships": "Общий разбор темы отношений: потребности, сценарии, подходящий партнёр.",
    "work": "Общий разбор темы работы и денег: способности, формат работы, отношение к деньгам.",
    "current_period": "Какие темы выделяются в ближайший месяц? Что усиливается, где терять силы, что делать.",
}


def _parse_date(raw: str) -> date | None:
    """ДД.ММ.ГГГГ или ГГГГ-ММ-ДД (браузерный <input type=date> шлёт ISO)."""
    txt = (raw or "").strip()
    m = re.match(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$", txt)
    try:
        if m:
            d, mo, y = (int(x) for x in m.groups())
            return date(y, mo, d)
        return date.fromisoformat(txt)
    except ValueError:
        return None

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
.natalwrap{position:relative;width:100vw;left:50%;transform:translateX(-50%);margin:8px 0 6px;}
#natal{width:100%;height:88vh;min-height:540px;display:block;}
.natalcap{position:absolute;top:18px;left:0;right:0;z-index:2;text-align:center;
 font-family:'Space Mono',ui-monospace,monospace;color:var(--muted);
 font-size:12px;letter-spacing:.18em;text-transform:uppercase;margin-bottom:6px;}
.natalfoot{text-align:center;font-family:'Space Mono',ui-monospace,monospace;color:var(--ink);
 font-size:15px;margin-top:10px;letter-spacing:.04em;}
.natalleg{max-width:560px;margin:18px auto 0;display:grid;grid-template-columns:1fr 1fr;gap:0 26px;}
.lgrow{display:flex;align-items:baseline;gap:11px;padding:8px 2px;border-bottom:1px solid var(--line);}
.lgico{color:#e8c168;font-size:18px;width:22px;text-align:center;flex:none;}
.lgrow b{color:var(--ink);font-weight:600;min-width:78px;font-size:14.5px;}
.lgpos{color:var(--muted);font-family:'Space Mono',ui-monospace,monospace;font-size:13px;margin-left:auto;}
.natalhint{max-width:560px;margin:14px auto 0;text-align:center;color:var(--muted);font-size:12.5px;}
@media(max-width:560px){.natalleg{grid-template-columns:1fr;}}
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
    "<link rel=icon href='/static/particles/mark.svg?v=20260914c' type='image/svg+xml'>"
    "<link rel=preconnect href='https://fonts.googleapis.com'>"
    "<link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
    "<link rel=stylesheet href='https://fonts.googleapis.com/css2?"
    "family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&"
    "family=Manrope:wght@400;500;600;700&"
    "family=IBM+Plex+Mono:wght@400;500&display=swap'>"
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
    field_index = 0
    def connect_label(match):
        nonlocal field_index
        field_index += 1
        field_id = f"field-{field_index}"
        return f'<label for="{field_id}">{match[1]}</label><{match[2]} id="{field_id}"'
    body = re.sub(r"<label>(.*?)</label><(input|select)\b", connect_label, body)
    return (
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{_FONTS}"
        f"<style>{_CSS}{_HERO_CSS}{_POLISH_CSS}</style>"
        f"<link rel=stylesheet href='/static/pages.css?v=20260914b'>"
        f"<link rel=stylesheet href='/static/mystic.css?v=20260914b'>"
        f"<link rel='stylesheet' href='/static/particles.css?v=20260914c'>"
        f"<script src='/static/reading.js?v=20260914b' defer></script>{head_extra}</head>"
        f"<body>{body}{_REVEAL_JS}</body></html>"
    )


def _nav() -> str:
    return (
        "<div class=wrap><div class=nav><a class=brand href='/'><img class=brand-emblem src='/static/particles/mark.svg?v=20260914c' alt='' width=30 height=30>Матрица<span>.</span></a>"
        "<div class=navlinks><a href='/compat'>Совместимость</a>"
        "<a href='/event'>Выбор даты</a><a href='/history'>Мои разборы</a>"
        "<a href='/about'>Как это работает</a></div></div></div>"
    )


# Общая шапка страниц; окончательные цвета и размеры задаёт static/pages.css.
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


_TOPIC_ART = {
    "personality": "self", "relationships": "relationships", "work": "vocation",
    "current_period": "rhythm", "compatibility": "resonance", "event": "moment",
}


def _hero(title: str, kicker: str = "", sub: str = "", art: str = "") -> str:
    """Shared page heading with an optional topic-specific vector engraving."""
    k = f"<p class=kicker>{html.escape(kicker)}</p>" if kicker else ""
    s = f"<p class=sub>{html.escape(sub)}</p>" if sub else ""
    asset = _TOPIC_ART.get(art)
    emblem = (f"<img class=page-emblem src='/static/particles/{asset}.svg?v=20260914c' "
              "alt='' aria-hidden=true width=640 height=640>") if asset else ""
    css = "chero has-emblem" if asset else "chero"
    return f"<section class='{css}'>{emblem}<div class=inner>{k}<h1>{title}</h1>{s}</div></section>"


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
    safe_pid = html.escape(pid, quote=True)
    started = _job_started.get(pid, time.time())
    body = f"""{_nav()}<main class=waiting-main data-job-id='{safe_pid}' data-started='{started}'>
      <div class=waiting-inner><div class=waiting-orb aria-hidden=true></div>
      <div class=waiting-eyebrow>Твоя карта становится историей</div>
      <h1>Собираем твой разбор</h1>
      <p id=waiting-status role=status>Рассчитываем карту и составляем персональный текст.</p>
      <p>Обычно это занимает несколько минут. Можно оставить страницу открытой — готовый разбор появится автоматически.</p>
      <div class=waiting-time id=waiting-time>Ожидание ответа…</div>
      <div class=waiting-actions><a href='/history'>Мои разборы</a><a href='/'>На главную</a></div>
      </div></main>"""
    head = f"<noscript><meta http-equiv=refresh content='15;url=/r/{safe_pid}'></noscript>"
    return _page("Твой разбор готовится · Матрица", body, head)


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
def landing(request: Request) -> str:
    # Семантический HTML без сборки; форма отправляется на /report.
    if _LANDING_HTML:
        if request.url.hostname in {"127.0.0.1", "localhost", "::1"} and not config.ai_ready():
            banner = "<div class=local-preview-banner>Локальное превью: AI здесь не подключён. <a href='https://web-production-b18f0.up.railway.app/'>Открыть рабочий сайт с разбором ↗</a></div>"
            return _LANDING_HTML.replace("<body>", "<body>" + banner)
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
    body = f"""{_nav()}{_hero('Как устроен твой разбор', 'От данных к интерпретации', 'Несколько традиций, понятный язык и пространство для твоего собственного взгляда.')}
    <main class=wrap>
      <section class=sec><h2>Сначала вопрос, потом система</h2><p>Не обязательно выбирать астрологическую школу заранее. Начни с вопроса о себе, отношениях, работе или текущем периоде. Программа рассчитает доступные показатели по твоим данным, а под разбором перечислит использованные системы.</p></section>
      <div class=method-list>
        <section class=sec><h2>Джйотиш</h2><p>Индийская астрологическая традиция. Использует сидерический зодиак, положение Луны и систему жизненных периодов для символической интерпретации карты.</p></section>
        <section class=sec><h2>Западная астрология</h2><p>В нашем расчёте — тропический зодиак, планеты и, при наличии времени и места рождения, дома и асцендент. Даёт свой язык для разговора о характере и отношениях.</p></section>
        <section class=sec><h2>Ба Цзы</h2><p>Китайская календарная система, которая описывает момент рождения через столпы и пять элементов. Её интерпретации могут отличаться от астрологических.</p></section>
        <section class=sec><h2>Нумерология и арканы</h2><p>Символические подходы, которые связывают числа даты рождения с темами и образами. Дополняют разбор вопросами для размышления.</p></section>
      </div>
      <section class=sec><h2>Где здесь нейросеть</h2><p>Расчёт выполняет программа. Нейросеть получает доступные результаты и составляет текст по выбранному вопросу. Если данных для системы не хватает, она не должна выдавать её показатели за рассчитанные.</p><p>Даже при одинаковых исходных данных формулировки текста могут различаться. Расчёт при тех же параметрах остаётся воспроизводимым.</p></section>
      <section class=sec><h2>Что означает точность</h2><p>Точность положения планет не доказывает точность выводов о личности или будущем. Астрологическая интерпретация не является научно подтверждённой диагностикой и не гарантирует события. Сравнивай текст со своим опытом: с ним можно не соглашаться.</p><a class=inline-link href='/proof'>Посмотреть, как рассчитываются положения планет →</a></section>
      <section class=sec id=data><h2>Как используются твои данные</h2><p>Имя, пол, дата, время и город рождения, если они указаны, поступают на сервер для расчёта. Имя и пол нужны для обращения, а выбранная тема и вопрос — для содержания.</p><p>Данные профиля и результаты расчёта передаются через OpenRouter модели, которая составляет текст. Профиль и готовый разбор сохраняются в серверной базе Supabase.</p><p>Готовый результат открывается по индивидуальной ссылке без входа в аккаунт. Любой, у кого есть эта ссылка, сможет прочитать разбор. Передавай её только тем, с кем хочешь им поделиться.</p><p>В разделе «Мои разборы» хранятся только ссылки и названия, которые ты сохранишь на этом устройстве. Дата, время и город рождения туда не записываются. Для исправления исходных данных создай новый разбор. Уточняющий вопрос передаётся той же модели вместе с текстом готового разбора; ответ показывается на странице.</p></section>
      <p class=foot><a class=cta href='/#form'>Перейти к своему вопросу ↗</a></p>
    </main>"""
    return _page("Как это работает · Матрица", body)


@router.get("/proof", response_class=HTMLResponse)
def proof() -> str:
    geo = {"lat": 51.4769, "lon": 0.0, "timezone": "UTC"}
    sample = {"calculation_modules": {"western_astrology": astrology.western(date(2000, 1, 1), "12:00", geo)}}
    body = f"""{_nav()}{_hero('Что именно мы рассчитываем', 'Расчёт и его границы', 'Положение планет можно вычислить. Значение, которое мы ему придаём, — уже интерпретация.')}
    <main class=wrap><section class=sec><h2>Воспроизводимые координаты</h2><p>Для астрономической части используется Swiss Ephemeris. Дата, время и координаты места задают исходные условия. При одинаковых параметрах и настройках программа возвращает одинаковые положения планет.</p><p>Без точного времени и места рождения часть показателей недоступна. При приблизительном времени асцендент и дома могут измениться.</p><h2>Чего расчёт не подтверждает</h2><p>Точность координат не означает, что по ним можно достоверно узнать характер, дату брака или будущий доход. Астрологический текст — символическая интерпретация, которую стоит сопоставлять со своим опытом.</p></section><div class=sectionhead>Пример: 1 января 2000, 12:00 UTC, Гринвич</div>{_positions_html(sample)}<p class=note>Положения ниже рассчитаны тем же модулем, который используется для персональных разборов.</p><p class=foot><a class=cta href='/#form'>Начать свой разбор ↗</a></p></main>"""
    return _page("Расчёт и его границы · Матрица", body)


@router.get("/example", response_class=HTMLResponse)
def example() -> str:
    body = f"""{_nav()}{_hero('Твоя опора — умение видеть глубже', 'Пример разбора', 'Обо мне · Какие мои сильные стороны?')}
    <main class='wrap report-main'><div class=cred>Редакционный пример формата. Это вымышленная иллюстрация текста, без персонального расчёта и привязки к дате рождения.</div>
    <section class=sec><h2>Коротко</h2><p>Возможно, тебе важно сначала понять смысл, а уже потом действовать. Это помогает замечать детали, которые другие пропускают.</p><p>Внимательность становится силой, когда не превращается в бесконечную проверку. Тебе может быть легче раскрыться там, где есть свобода выбирать свой ритм.</p></section>
    <nav class=toc aria-label='Разделы примера'><a href='#strengths'>Сильные стороны</a><a href='#friction'>Что может мешать</a><a href='#first-step'>Первый шаг</a></nav>
    <section class=sec id=strengths><h2>На что можно опереться</h2><p>Представь задачу, в которой не хватает ясности. Возможно, ты замечаешь противоречия, задаёшь точные вопросы и постепенно собираешь целую картину. В работе это может проявляться как внимание к смыслу, а в отношениях — как интерес к тому, что человек на самом деле хочет сказать.</p><p>Проверь по своему опыту: за какой помощью к тебе чаще всего обращаются? Ответ может подсказать, какие способности окружающие уже замечают.</p></section>
    <section class=sec id=friction><h2>Что может мешать</h2><p>Иногда поиск полной уверенности откладывает первый шаг. Если это знакомо, попробуй отделить то, что нужно выяснить сейчас, от того, что можно узнать в процессе.</p><p>Это не означает, что тебе нужно всегда действовать быстрее. В некоторых ситуациях именно пауза и внимательная проверка помогают.</p></section>
    <section class=sec id=first-step><h2>Маленький шаг на сегодня</h2><p>Вспомни ситуацию, в которой тебе было легко. Запиши, что помогло: люди, темп, ясная задача или свобода решений. Выбери одно условие, которое можно повторить на этой неделе.</p><p>Это общее упражнение для размышления, а не астрологический прогноз. Если описание не откликается, используй его как повод сформулировать свой ответ.</p></section>
    <div class=profile-actions><a class=cta href='/#form'>Теперь мой разбор ↗</a><a class=btnlink href='/about'>Как это работает</a></div><p class=foot>Твой персональный разбор будет основан на введённых данных, доступных расчётах и выбранном вопросе.</p></main>"""
    return _page("Пример разбора · Матрица", body)


_PUBLIC_SITE = "https://web-production-b18f0.up.railway.app"
_job_started: dict[str, float] = {}
_job_requests: dict[str, ProfileRequest] = {}
_retry_targets: dict[str, str] = {}
_jobs_lock = threading.RLock()
_clarify_busy: set[str] = set()
_clarify_times: OrderedDict[str, list[float]] = OrderedDict()


def _register_job(pid: str, req: ProfileRequest | None = None) -> None:
    # Bound in-process bookkeeping; completed profiles live in the existing database.
    with _jobs_lock:
        while len(_job_started) >= 512:
            oldest = next(iter(_job_started))
            _job_started.pop(oldest, None)
            _job_requests.pop(oldest, None)
            _web_status.pop(oldest, None)
            _retry_targets.pop(oldest, None)
        _job_started[pid] = time.time()
        _web_status[pid] = "pending"
        if req is not None:
            _job_requests[pid] = req


def _provider_unavailable() -> HTMLResponse:
    body = f"{_nav()}<main class=wrap><div class=error-panel><h1>На этой версии разбор недоступен</h1><p>Здесь не подключён сервис интерпретации. Мы не будем выдавать расчётные данные за готовый персональный текст.</p><a class=cta href='{_PUBLIC_SITE}/#form'>Открыть рабочий сайт ↗</a></div></main>"
    return HTMLResponse(_page("Разбор недоступен · Матрица", body), status_code=503)


def _report_ready(data: dict) -> bool:
    report = data.get("report") or {}
    status = report.get("generation_status")
    if status is not None:
        return status == "ready" and bool(report.get("full_report"))
    text = (report.get("short_summary", "") + "\n" + report.get("full_report", ""))
    legacy_errors = ("OPENROUTER_API_KEY", "AI-портрет не собрался", "AI-синтез временно недоступен", "Отчёт недоступен")
    return bool(report.get("full_report")) and not any(marker in text for marker in legacy_errors)


def _failed_page(pid: str, data: dict | None = None) -> HTMLResponse:
    safe_pid = html.escape(pid, quote=True)
    atype = ((data or {}).get("user_input") or {}).get("analysis_type", "personality")
    can_retry = pid in _job_requests or (data and atype in _GENERAL_QUESTION)
    action = f"<form method=post action='/r/{safe_pid}/retry'><button type=submit>Повторить с теми же данными</button></form>" if can_retry else "<a class=cta href='/#form'>Вернуться к анкете</a>"
    body = f"{_nav()}<main class=wrap><div class=error-panel><h1>Разбор не удалось завершить</h1><p>Сервис не вернул полный текст. Это не готовый разбор; повторная попытка запустит составление заново.</p><div class=error-actions>{action}<a class=btnlink href='/history'>Мои разборы</a></div></div></main>"
    return HTMLResponse(_page("Разбор не завершён · Матрица", body), status_code=503)


@router.get("/r/{pid}/status")
def reading_status(pid: str) -> Response:
    data = database.get_profile(pid)
    if data is not None:
        state = "ready" if _report_ready(data) else "failed"
    elif _web_status.get(pid) == "pending":
        state = "processing"
    elif _web_status.get(pid, "").startswith("error:"):
        state = "failed"
    else:
        return JSONResponse({"status": "not_found"}, status_code=404, headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": state}, headers={"Cache-Control": "no-store"})


@router.post("/r/{pid}/retry")
def retry_reading(pid: str, background_tasks: BackgroundTasks) -> Response:
    if not config.ai_ready():
        return _provider_unavailable()
    if pid in _retry_targets:
        return RedirectResponse(f"/r/{_retry_targets[pid]}", status_code=303)
    data = database.get_profile(pid)
    if data and _report_ready(data):
        return RedirectResponse(f"/r/{pid}", status_code=303)
    if _web_status.get(pid) == "pending":
        return RedirectResponse(f"/r/{pid}", status_code=303)
    req = _job_requests.get(pid)
    if req is None and data:
        try:
            req = ProfileRequest.model_validate(data.get("user_input") or {})
        except ValueError:
            req = None
    if req is None or req.analysis_type.value not in _GENERAL_QUESTION:
        return RedirectResponse('/#form', status_code=303)
    # Database reads run in parallel worker threads. Recheck inside the lock so
    # two clicks cannot schedule two paid generations for the same failed job.
    with _jobs_lock:
        if pid in _retry_targets:
            return RedirectResponse(f"/r/{_retry_targets[pid]}", status_code=303)
        new_pid = uuid.uuid4().hex
        _register_job(new_pid, req)
        while len(_retry_targets) >= 512:
            _retry_targets.pop(next(iter(_retry_targets)))
        _retry_targets[pid] = new_pid
    background_tasks.add_task(_build_and_save, new_pid, req, "web/retry")
    return RedirectResponse(f"/r/{new_pid}", status_code=303)


@router.get("/history", response_class=HTMLResponse)
def reading_history() -> str:
    body = f"""{_nav()}{_hero('Твои сохранённые разборы', 'Моё пространство', 'Истории, к которым хочется вернуться.')}
    <main class=wrap><div id=history-list class=history-list></div><div id=history-empty class=history-empty><h2>Здесь появятся твои разборы</h2><p>В готовом результате нажми «Сохранить в мои разборы», чтобы вернуться к нему позже.</p><a class=cta href='/#form'>Начать свой разбор ↗</a></div><p class=history-note>Список хранится в этом браузере на этом устройстве. Это сохранённые ссылки, без синхронизации с другим телефоном или компьютером. Убирая запись из списка, ты не удаляешь сам разбор с сервера.</p><noscript><p>Для списка сохранённых ссылок нужен JavaScript.</p></noscript><p class=foot><a href='/about#data'>Как используются данные</a></p></main>"""
    return _page("Мои разборы · Матрица", body)


def _reading_tools(pid: str, title: str) -> str:
    meta = _json.dumps({"id": pid, "title": html.unescape(title)}, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<div class=report-status-line>Разбор готов · ссылка ведёт к сохранённому результату</div>
    <script id=reading-meta type='application/json'>{meta}</script>
    <div class=report-tools><button type=button id=remember-reading>Сохранить в мои разборы</button><button type=button id=print-reading>Сохранить PDF</button><a class=btnlink href='/r/{html.escape(pid, quote=True)}/download'>Скачать текст</a><button type=button id=copy-reading>Копировать ссылку</button><span class=tool-status id=tool-status role=status></span></div>"""


def _followup_form(pid: str) -> str:
    return f"""<section class=followup-card><h2>Хочется понять глубже?</h2><p>Задай один вопрос по этому разбору или попроси объяснить проще.</p><div class=followup-presets><button type=button data-followup='Объясни главную мысль моего разбора проще, на одном жизненном примере.'>Объяснить проще</button><button type=button data-followup='Как применить главную мысль этого разбора в обычной жизни? Дай один небольшой шаг.'>Как применить</button><button type=button data-followup='Какие вопросы мне стоит задать себе, чтобы проверить, откликается ли этот разбор?'>Вопросы к себе</button></div><form id=followup-form data-reading-id='{html.escape(pid, quote=True)}'><label for=followup-question>Твой вопрос</label><textarea id=followup-question name=question maxlength=500 required placeholder='Что это значит в моей ситуации?'></textarea><button type=submit>Задать вопрос</button><p id=followup-status role=status class=followup-note></p></form><div class=followup-answer id=followup-answer></div><p class=followup-note>Уточнение опирается на готовый текст. Для другой темы создай отдельный разбор. Ответ на уточнение не сохраняется при обновлении страницы.</p></section>"""


@router.get("/r/{pid}/download")
def download_reading(pid: str) -> Response:
    data = database.get_profile(pid)
    if not data or not _report_ready(data):
        return Response(status_code=404)
    ui = data.get("user_input") or {}
    title = f"{ui.get('name') or 'Твой разбор'} · {_ANALYSIS_LABELS.get(ui.get('analysis_type'), 'Матрица')}"
    text = title + "\n\n" + data["report"]["full_report"] + "\n\nМатрица · Интерпретация для размышления."
    return Response(text, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="matrica-reading.txt"', "Cache-Control": "no-store"})


@router.post("/r/{pid}/clarify")
async def clarify_reading(pid: str, request: Request, question: str = Form(..., min_length=3, max_length=500)) -> Response:
    if request.headers.get("x-matrix-action") != "clarify":
        return JSONResponse({"error": "Отправь вопрос из формы под разбором."}, status_code=403)
    data = await run_in_threadpool(database.get_profile, pid)
    if not data or not _report_ready(data):
        return JSONResponse({"error": "Сначала дождись готового разбора."}, status_code=404)
    if not config.ai_ready():
        return JSONResponse({"error": "Сервис интерпретации сейчас недоступен."}, status_code=503)
    question = question.strip()
    if len(question) < 3:
        return JSONResponse({"error": "Напиши вопрос из нескольких слов."}, status_code=422)
    now = time.monotonic()
    recent = [t for t in _clarify_times.get(pid, []) if now - t < 600]
    if pid in _clarify_busy or len(_clarify_busy) >= 3 or len(recent) >= 3:
        return JSONResponse({"error": "Подожди завершения ответа или вернись к уточнениям чуть позже."}, status_code=429)
    _clarify_times[pid] = recent + [now]
    _clarify_times.move_to_end(pid)
    while len(_clarify_times) > 512:
        _clarify_times.popitem(last=False)
    _clarify_busy.add(pid)
    try:
        answer = await run_in_threadpool(synthesis.clarify, data["report"]["full_report"], question, (data.get("user_input") or {}).get("gender", ""))
    except Exception as exc:
        await run_in_threadpool(database.log_error, "exception", "web/clarify", type(exc).__name__)
        answer = None
    finally:
        _clarify_busy.discard(pid)
    if not answer:
        return JSONResponse({"error": "Не удалось получить полный ответ. Твой исходный разбор сохранён; попробуй позже."}, status_code=502)
    return JSONResponse({"answer": answer}, headers={"Cache-Control": "no-store"})


# ---------------- основной разбор ----------------
def _build_and_save(pid: str, req: ProfileRequest, where: str) -> None:
    try:
        profile = build_profile(req)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
        _web_status.pop(pid, None)
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", where, f"{type(e).__name__}: {e}")


def _form_error(message: str) -> HTMLResponse:
    body = f"{_nav()}<main class=wrap><div class=error-panel><h1>Проверь данные</h1><p>{html.escape(message)}</p><p><a class=cta href='/#form'>Вернуться к анкете</a></p></div></main>"
    return HTMLResponse(_page("Проверь данные · Матрица", body), status_code=422)


@router.post("/report", response_class=HTMLResponse)
def report(
    background_tasks: BackgroundTasks,
    name: str = Form(""),
    gender: str = Form(""),
    birth_date: str = Form(...),
    birth_time: str = Form(""),
    time_precision: str = Form("exact"),
    birth_place: str = Form(""),
    analysis_type: str = Form("personality"),
    main_request: str = Form("", max_length=300),
) -> str:
    if not config.ai_ready():
        return _provider_unavailable()
    bd = _parse_date(birth_date)
    if not bd or not date(1900, 1, 1) <= bd <= date.today():
        return _form_error("Укажи существующую дату рождения от 01.01.1900 до сегодняшнего дня в формате ДД.ММ.ГГГГ.")
    if gender.strip() not in {"ж", "м"}:
        return _form_error("Выбери пол, чтобы мы могли правильно обращаться к тебе.")
    if not name.strip() or len(name.strip()) > 80:
        return _form_error("Укажи имя длиной от 1 до 80 символов.")
    if len(birth_place.strip()) > 120:
        return _form_error("Укажи название города длиной до 120 символов.")
    if time_precision not in {"exact", "approx", "unknown"}:
        return _form_error("Выбери, насколько точно известно время рождения.")
    try:
        atype = AnalysisType(analysis_type)
    except ValueError:
        atype = AnalysisType.personality
    if atype not in {AnalysisType.personality, AnalysisType.relationships, AnalysisType.work, AnalysisType.current_period}:
        return _form_error("Выбери тему из формы. Для совместимости и выбора даты есть отдельные страницы.")
    bt = None if time_precision == "unknown" else (birth_time.strip() or None)
    if bt and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", bt):
        return _form_error("Укажи время в формате ЧЧ:ММ, например 14:30, или выбери «Не знаю».")
    period = None
    if atype == AnalysisType.current_period:
        today = date.today()
        period = (today, date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))
    req = ProfileRequest(
        name=name.strip(), gender=gender.strip(), birth_date=bd,
        birth_time=bt, time_precision=(time_precision if bt else "unknown"),
        birth_place=(birth_place.strip() or None),
        period_from=period[0] if period else None, period_to=period[1] if period else None,
        main_request=main_request.strip() or _GENERAL_QUESTION.get(atype.value, ""), analysis_type=atype,
    )
    pid = uuid.uuid4().hex
    _register_job(pid, req)
    background_tasks.add_task(_build_and_save, pid, req, "web/report")
    return RedirectResponse(f"/r/{pid}", status_code=303)


# ---------------- совместимость ----------------
@router.get("/compat", response_class=HTMLResponse)
def compat_form() -> str:
    body = f"""{_nav()}{_hero('Совместимость', 'Разбор с конкретным человеком', 'Где вы усиливаете друг друга, где задеваете и как общаться — без приговора «вместе/нет».', 'compatibility')}
    <div class=wrap id=form>
      <div class=formcard>
        <div class=tabbar>
          <a class=tab href='/#form'>Обо мне / отношения / работа / период</a>
          <span class='tab on'>Совместимость</span>
          <a class=tab href='/event'>Выбор даты</a>
        </div>
        <form method=post action='/compat/run'>
          <div class=row>
            <div><label>Твоё имя</label><input name=name_a required></div>
            <div><label>Твой пол</label><select name=gender_a required><option value='' disabled selected>Выбери</option><option value='ж'>Женщина</option><option value='м'>Мужчина</option></select></div>
          </div>
          <div class=row>
            <div><label>Твоя дата рождения</label><input name=date_a type=date required></div>
            <div></div>
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
          <button type=submit>Получить разбор</button>
          <p class=note>Разбор показывает, где вы усиливаете друг друга и где задеваете, — это интерпретация, а не вердикт «вместе/нет».</p>
        </form>
      </div>
    </div>"""
    return _page("Совместимость · Матрица", body)


def _build_synastry_and_save(pid: str, a: ProfileRequest, b: ProfileRequest) -> None:
    try:
        profile = build_synastry(a, b)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
        _web_status.pop(pid, None)
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", "web/compat", f"{type(e).__name__}: {e}")


@router.post("/compat/run", response_class=HTMLResponse)
def compat_run(
    background_tasks: BackgroundTasks,
    name_a: str = Form(""), gender_a: str = Form(""), date_a: str = Form(...), time_a: str = Form(""), place_a: str = Form(""),
    name_b: str = Form(""), date_b: str = Form(...), time_b: str = Form(""), place_b: str = Form(""),
) -> str:
    if not config.ai_ready():
        return _provider_unavailable()
    bda, bdb = _parse_date(date_a), _parse_date(date_b)
    if not bda or not bdb:
        return _page("Ошибка", f"{_nav()}<div class=wrap><h1>Неверная дата</h1><a href='/compat'>← назад</a></div>")
    a = ProfileRequest(name=name_a.strip(), gender=gender_a.strip(), birth_date=bda, birth_time=(time_a.strip() or None),
                       birth_place=(place_a.strip() or None), analysis_type=AnalysisType.compatibility)
    b = ProfileRequest(name=name_b.strip(), birth_date=bdb, birth_time=(time_b.strip() or None),
                       birth_place=(place_b.strip() or None), analysis_type=AnalysisType.compatibility)
    pid = uuid.uuid4().hex
    _register_job(pid)
    background_tasks.add_task(_build_synastry_and_save, pid, a, b)
    return RedirectResponse(f"/r/{pid}", status_code=303)


# ---------------- сделка / событие ----------------
@router.get("/event", response_class=HTMLResponse)
def event_form() -> str:
    body = f"""{_nav()}{_hero('Выбор даты', 'Подходит ли день для дела', 'Оценка конкретной даты для сделки, переговоров, запуска или важного разговора.', 'event')}
    <div class=wrap id=form>
      <div class=formcard>
        <div class=tabbar>
          <a class=tab href='/#form'>Обо мне / отношения / работа / период</a>
          <a class=tab href='/compat'>Совместимость</a>
          <span class='tab on'>Выбор даты</span>
        </div>
        <form method=post action='/event/run'>
          <div class=row>
            <div><label>Имя</label><input name=name required></div>
            <div><label>Пол</label><select name=gender required><option value='' disabled selected>Выбери</option><option value='ж'>Женщина</option><option value='м'>Мужчина</option></select></div>
          </div>
          <div class=row>
            <div><label>Дата рождения</label><input name=birth_date type=date required></div>
            <div><label>Время рождения (по желанию)</label><input name=birth_time placeholder='14:30'></div>
          </div>
          <div class=row>
            <div><label>Город рождения (по желанию)</label><input name=birth_place placeholder='Москва'></div>
            <div></div>
          </div>
          <hr>
          <div class=row>
            <div><label>Дата события</label><input name=event_date type=date required></div>
            <div><label>Что за событие</label><input name=event_desc placeholder='подписание сделки' required></div>
          </div>
          <button type=submit>Оценить дату</button>
          <p class=note>Символическая интерпретация выбранного дня. Она не гарантирует исход события и не заменяет оценку реальных обстоятельств.</p>
        </form>
      </div>
    </div>"""
    return _page("Выбор даты · Матрица", body)


def _build_event_and_save(pid: str, req: ProfileRequest, ev: date, desc: str) -> None:
    try:
        profile = build_event(req, ev, desc)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
        _web_status.pop(pid, None)
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", "web/event", f"{type(e).__name__}: {e}")


@router.post("/event/run", response_class=HTMLResponse)
def event_run(
    background_tasks: BackgroundTasks,
    name: str = Form(""), gender: str = Form(""), birth_date: str = Form(...), birth_time: str = Form(""),
    birth_place: str = Form(""), event_date: str = Form(...), event_desc: str = Form(""),
) -> str:
    if not config.ai_ready():
        return _provider_unavailable()
    bd, ev = _parse_date(birth_date), _parse_date(event_date)
    if not bd or not ev:
        return _page("Ошибка", f"{_nav()}<div class=wrap><h1>Неверная дата</h1><a href='/event'>← назад</a></div>")
    req = ProfileRequest(name=name.strip(), gender=gender.strip(), birth_date=bd, birth_time=(birth_time.strip() or None),
                         birth_place=(birth_place.strip() or None), main_request=event_desc.strip(),
                         analysis_type=AnalysisType.event)
    pid = uuid.uuid4().hex
    _register_job(pid)
    background_tasks.add_task(_build_event_and_save, pid, req, ev, event_desc.strip())
    return RedirectResponse(f"/r/{pid}", status_code=303)


# ---------------- результат ----------------
def _render_sections(full: str) -> tuple[str, str]:
    """Markdown отчёта → (оглавление, карточки-секции по ##). «Коротко» показан выше отдельно."""
    parts = re.split(r"(?m)^## ", full or "")
    toc, cards = [], []
    for i, part in enumerate(parts[1:]):
        title = part.split("\n", 1)[0].strip()
        if title.lower() == synthesis.SHORT_SECTION.lower():
            continue
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
        state = _web_status.get(pid, "")
        if state.startswith("error:"):
            return _failed_page(pid)
        if state == "pending":
            return _spinner_page(pid, "Собираем разбор", "")
        return HTMLResponse(_page("Разбор не найден · Матрица", f"{_nav()}<main class=wrap><div class=error-panel><h1>Не нашли этот разбор</h1><p>Ссылка могла относиться к локальному превью или к незавершённому разбору. Открой сохранённый результат или создай новый.</p><div class=error-actions><a class=cta href='/#form'>Начать разбор</a><a class=btnlink href='/history'>Мои разборы</a></div></div></main>"), status_code=404)
    if not _report_ready(data):
        return _failed_page(pid, data)

    ui = data.get("user_input") or {}
    rep = data.get("report") or {}
    atype_val = ui.get("analysis_type") or ""
    label = _ANALYSIS_LABELS.get(atype_val, "")
    sub = ""
    if atype_val == "current_period" and ui.get("period_from"):
        sub = f"Период: с {date_ru(ui['period_from'])} по {date_ru(ui['period_to'])}"
    title = html.escape(ui.get("name") or "Профиль") + (f" · {html.escape(label)}" if label else "")
    full = rep.get("full_report") or ""
    short_md = synthesis.short_section(full) or (rep.get("short_summary") or "").strip()
    summary = _md_to_html(short_md)
    toc_html, cards = _render_sections(full)
    mods = data.get("calculation_modules") or {}
    basis = synthesis.basis_line(mods.get("person_a") or mods if "person_a" in mods else mods)
    tech = rep.get("tech_methods") or ""
    advanced = (f"<details><summary>Расчёт: что именно посчитано и по каким системам</summary>"
                f"{_md_to_html(tech)}</details>") if tech else ""
    positions = _positions_html(data)
    body = f"""{_nav()}{_hero(title, 'Твой разбор', sub, atype_val)}
    <main class="wrap report-main">
      {f'<section class=sec><h2>Коротко</h2>{summary}</section>' if summary else ''}
      {f'<div class=cred>{html.escape(basis)}</div>' if basis else ''}
      {_reading_tools(pid, title)}
      <div class=actions>
        <a class=btnlink href='/profile/{pid}'>Моя карта в цифрах</a>
        <a class=btnlink href='/voice/{pid}.mp3'>Слушать разбор</a>
        <a class=btnlink href='/#form'>Новый вопрос</a>
        <a class=btnlink href='/compat'>Совместимость</a>
      </div>
      <h2 class=more>Подробная расшифровка</h2>
      {toc_html}
      {cards}
      {_followup_form(pid)}
      <h2 class=more>Карта и расчёт</h2>{_natal_block(data, pid)}<details><summary>Положения планет</summary>{positions}</details>
      {advanced}
      <p class=foot>Это интерпретация, а не вывод о тебе: если что-то не откликается — так бывает. Важные решения о здоровье, деньгах и отношениях остаются за тобой.</p>
    </main>"""
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
