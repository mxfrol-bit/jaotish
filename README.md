# Matrix Engine

Закрытая AI-система системного профайлинга. Не гадание: числа и арканы считаются
детерминированным кодом, AI лишь собирает единый портрет поверх рассчитанных данных
и честно отмечает, какие методики ещё не подключены.

Стек: **FastAPI + Supabase + python-telegram-bot** (бот и API в одном процессе),
AI-синтез через **OpenRouter**, деплой на **Railway** из GitHub.

## Что считается в v0.1

| Модуль | Статус | Источник |
|---|---|---|
| Нумерология (пифагорейская) | ✅ calculated | свой код |
| 22 аркана (центральный крест) | ✅ calculated | свой код, формулы в `config/arcana.json` |
| Западная астрология / Джйотиш / Ба Цзы | ⛔ not_connected | подключаются в v0.2–0.3 |

**Кардинальное правило:** AI интерпретирует только поля со статусом `calculated`.
Нет модуля — поле получает статус, а не выдуманное значение.

## Структура

```
app/
  main.py            FastAPI + запуск Telegram-бота (polling) в lifespan
  config.py          переменные окружения
  models.py          схема профиля + enum calculation_status
  calc/matrix_calc.py детерминированный расчёт (нумерология + 22 аркана)
  synthesis.py       AI-синтез через OpenRouter (только calculated-поля)
  engine.py          сборка профиля end-to-end
  database.py        Supabase (хранение профилей и обратной связи)
  bot.py             Telegram-бот (диалог сбора данных)
  routers/profiles.py HTTP API + _ver / _diag
config/              формулы и таблицы (нумерология, арканы) — без правки кода
supabase/schema.sql  таблицы me_profiles, me_feedback
```

## Локальный запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить ключи (можно без них — будет заглушка вместо AI)

# проверить расчётное ядро без зависимостей:
python -m app.calc.matrix_calc 1990-05-15 "Иван Иванов"

# поднять сервис:
uvicorn app.main:app --reload
```

## Переменные окружения

См. `.env.example`. Минимум для полного v0.1:
`OPENROUTER_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`.

## Деплой (Railway)

1. Подключить GitHub-репозиторий к новому проекту Railway.
2. Задать env-переменные из `.env.example`.
3. Start command берётся из `railway.toml`.
4. Проверка прода: `GET /api/profiles/_ver` и `GET /api/profiles/_diag?token=<DIAG_TOKEN>`.
