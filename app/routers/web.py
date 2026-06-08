"""Веб-приложение Matrix Engine: публичная страница разбора + админка багов.

Чистый светлый стиль (без градиентов/китча). HTML отдаём строками — без шаблонизатора,
чтобы не плодить зависимости. Разбор считается тем же движком, что и бот.
"""
from __future__ import annotations

import html
import re
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Form, Query
from fastapi.responses import HTMLResponse

from .. import config, database
from ..engine import build_profile
from ..models import AnalysisType, ProfileRequest
from ..synthesis import CREDIBILITY, METHOD_BASIS

router = APIRouter(tags=["web"])

# Статусы фоновой генерации по pid: "error:<текст>" — если упало (иначе ждём появления в БД).
_web_status: dict[str, str] = {}

_ANALYSIS_LABELS = {
    "personality": "Личность — базовый код",
    "current_period": "Текущий период",
    "work": "Деньги и реализация",
}

_CSS = """
:root{--ink:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--bg:#ffffff;--accent:#111111;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
 line-height:1.6;font-size:17px;}
.wrap{max-width:740px;margin:0 auto;padding:48px 22px 80px;}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em;margin:0 0 8px;}
h2{font-size:20px;font-weight:650;margin:30px 0 6px;letter-spacing:-.01em;}
.lead{color:var(--muted);font-size:18px;margin:0 0 28px;}
label{display:block;font-size:14px;color:var(--muted);margin:16px 0 6px;}
input,select{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:10px;
 font-size:16px;background:#fff;color:var(--ink);}
input:focus,select:focus{outline:none;border-color:#9ca3af;}
.row{display:flex;gap:14px;}.row>div{flex:1;}
button{margin-top:26px;width:100%;padding:14px 18px;border:0;border-radius:10px;
 background:var(--accent);color:#fff;font-size:16px;font-weight:600;cursor:pointer;}
button:hover{background:#000;}
.note{color:var(--muted);font-size:13px;margin-top:10px;}
.card{border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-top:22px;}
.summary{background:#fafafa;border:1px solid var(--line);border-radius:14px;padding:20px 22px;
 font-size:18px;margin:0 0 26px;}
hr{border:0;border-top:1px solid var(--line);margin:26px 0;}
p{margin:10px 0;}
a{color:#111;}
.back{display:inline-block;margin-top:30px;color:var(--muted);text-decoration:none;font-size:14px;}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:18px;}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top;}
th{color:var(--muted);font-weight:600;}
.kind{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#f3f4f6;}
.kind.exception{background:#fdecec;color:#a11;}
.kind.ai_failure{background:#fff4e5;color:#9a5b00;}
.kind.lang_leak{background:#eef2ff;color:#3730a3;}
.stat{display:inline-block;margin-right:18px;color:var(--muted);font-size:14px;}
.stat b{color:var(--ink);font-size:18px;}
.foot{color:var(--muted);font-size:13px;margin-top:40px;}
"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>"
    )


def _md_to_html(md: str) -> str:
    """Минимальный Markdown → HTML: ## заголовки, **жирный**, списки, абзацы."""
    out: list[str] = []
    for line in (md or "").split("\n"):
        s = line.rstrip()
        if s.startswith("## "):
            out.append(f"<h2>{html.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{html.escape(s[2:])}</h1>")
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


@router.get("/", response_class=HTMLResponse)
def landing() -> str:
    opts = "".join(
        f"<option value='{k}'>{html.escape(v)}</option>" for k, v in _ANALYSIS_LABELS.items()
    )
    body = f"""
    <h1>Матрица</h1>
    <p class=lead>Это не гороскоп. Система берёт точные параметры момента рождения,
    считает их по астрономическим эфемеридам космической точности и алгоритмически
    переводит в устойчивые модели поведения — твой «код»: сильные стороны, зоны риска,
    повторяющиеся сценарии. Расчёт на данных, а не гадание.</p>
    <p class=note>🛰 Положения светил берутся из тех же моделей, что используют в астрономии
    и спутниковой навигации. Никакой мистики — только расчёт и анализ паттернов.</p>
    <form method=post action='/report'>
      <label>Имя</label>
      <input name=name placeholder='Как тебя зовут' required>
      <label>Дата рождения</label>
      <input name=birth_date type=date required>
      <div class=row>
        <div><label>Время рождения (по желанию)</label>
          <input name=birth_time placeholder='14:30'></div>
        <div><label>Город рождения (по желанию)</label>
          <input name=birth_place placeholder='Москва'></div>
      </div>
      <label>Что смотрим</label>
      <select name=analysis_type>{opts}</select>
      <button type=submit>Показать мой код</button>
      <p class=note>Время и город нужны только для более тонкого слоя — без них тоже работает.</p>
    </form>
    <p class=foot>Матрица не ставит диагнозов и не предсказывает судьбу буквально.
    Важные решения о здоровье, деньгах и отношениях ты принимаешь сам.
    · <a href='/about'>Как это работает →</a></p>
    """
    return _page("Матрица", body)


@router.get("/about", response_class=HTMLResponse)
def about() -> str:
    body = (
        f"<div class=card>{_md_to_html(METHOD_BASIS)}</div>"
        "<a class=back href='/'>← к разбору</a>"
    )
    return _page("Как это работает · Матрица", body)


def _build_and_save(pid: str, req: ProfileRequest, analysis_type: str) -> None:
    """Считает разбор в фоне и сохраняет под заданным pid. Ошибку кладёт в _web_status."""
    try:
        profile = build_profile(req)
        profile.profile_id = pid
        database.save_profile(profile.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001
        _web_status[pid] = f"error:{type(e).__name__}: {e}"
        database.log_error("exception", "web/report", f"{type(e).__name__}: {e}",
                           {"analysis_type": analysis_type})


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
        return _page("Ошибка", "<h1>Неверная дата</h1><p>Формат: ГГГГ-ММ-ДД.</p>"
                     "<a class=back href='/'>← назад</a>")
    try:
        atype = AnalysisType(analysis_type)
    except ValueError:
        atype = AnalysisType.personality

    req = ProfileRequest(
        name=name.strip(),
        birth_date=bd,
        birth_time=(birth_time.strip() or None),
        birth_place=(birth_place.strip() or None),
        main_request=_ANALYSIS_LABELS.get(analysis_type, ""),
        analysis_type=atype,
    )
    pid = uuid.uuid4().hex
    # Считаем в фоне (это ~минута: геокод + расчёт + сборка текста), страница сразу отвечает.
    background_tasks.add_task(_build_and_save, pid, req, analysis_type)
    return _loading_page(pid, name)


def _loading_page(pid: str, name: str) -> str:
    body = f"""
    <h1>Собираю твой код…</h1>
    <p class=lead>Читаю слои и перевожу их на простой язык. Обычно это занимает
    до минуты — страница обновится сама.</p>
    <div class=spinner></div>
    <p class=note>{html.escape(name or '')}</p>
    """
    extra = f"<meta http-equiv='refresh' content='4;url=/r/{pid}'>"
    spin = ("<style>.spinner{width:34px;height:34px;border:3px solid #e5e7eb;"
            "border-top-color:#111;border-radius:50%;margin:28px 0;"
            "animation:sp 0.8s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}</style>")
    html_doc = _page("Собираю…", body).replace("</head>", extra + spin + "</head>")
    return html_doc


@router.get("/r/{pid}", response_class=HTMLResponse)
def result(pid: str) -> str:
    data = database.get_profile(pid)
    if data is None:
        # ещё считается или упало
        st = _web_status.get(pid, "")
        if st.startswith("error:"):
            _web_status.pop(pid, None)
            return _page("Ошибка", "<h1>Не получилось собрать разбор</h1>"
                         f"<p class=note>{html.escape(st[6:][:160])}</p>"
                         "<a class=back href='/'>← попробовать снова</a>")
        body = ("<h1>Ещё собираю…</h1><p class=lead>Почти готово — страница обновится сама.</p>"
                "<div class=spinner></div>")
        spin = ("<style>.spinner{width:34px;height:34px;border:3px solid #e5e7eb;"
                "border-top-color:#111;border-radius:50%;margin:28px 0;"
                "animation:sp 0.8s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}</style>")
        return _page("Собираю…", body).replace(
            "</head>", f"<meta http-equiv='refresh' content='4;url=/r/{pid}'>" + spin + "</head>")

    ui = data.get("user_input") or {}
    rep = data.get("report") or {}
    label = _ANALYSIS_LABELS.get(ui.get("analysis_type") or "", "")
    summary = html.escape((rep.get("short_summary") or "").strip())
    body_html = _md_to_html(rep.get("full_report") or "")
    title = f"{html.escape(ui.get('name') or 'Профиль')}" + (f" · {html.escape(label)}" if label else "")
    body = (
        f"<h1>{title}</h1>"
        + f"<p class=note>{html.escape(CREDIBILITY)}</p>"
        + (f"<div class=summary>{summary}</div>" if summary else "")
        + f"<div class=card>{body_html}</div>"
        + "<a class=back href='/'>← новый разбор</a>"
    )
    return _page("Твой разбор", body)


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request, token: str = Query("")) -> str:
    if not config.DIAG_TOKEN or token != config.DIAG_TOKEN:
        return _page("Админка", "<h1>Доступ закрыт</h1>"
                     "<p class=note>Добавь ?token=DIAG_TOKEN в адрес.</p>")
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
        where = html.escape(r.get("where_") or "")
        msg = html.escape((r.get("message") or "")[:300])
        tid = r.get("telegram_id") or ""
        trs.append(
            f"<tr><td>{when}</td><td><span class='kind {kind}'>{kind}</span></td>"
            f"<td>{where}</td><td>{msg}</td><td>{tid}</td></tr>"
        )
    table = (
        "<table><tr><th>Когда</th><th>Тип</th><th>Где</th><th>Сообщение</th><th>TG</th></tr>"
        + ("".join(trs) or "<tr><td colspan=5 class=note>Пока чисто — ошибок нет.</td></tr>")
        + "</table>"
    )
    body = f"<h1>Админка · баги</h1><div>{chips}</div>{table}<p class=foot>Последние 150 записей.</p>"
    return _page("Админка", body)
