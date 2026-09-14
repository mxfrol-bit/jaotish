/* Result lifecycle and explicit device-local bookmarks. No credentials or birth data. */
(() => {
  "use strict";
  const job = document.querySelector("[data-job-id]");
  if (job) {
    const id = job.dataset.jobId;
    const status = document.getElementById("waiting-status");
    const elapsed = document.getElementById("waiting-time");
    const start = Number(job.dataset.started) * 1000 || Date.now();
    const deadline = Date.now() + 8 * 60 * 1000;
    let stopped = false;
    const timer = setInterval(() => {
      const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
      elapsed.textContent = `Прошло ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);
    async function poll() {
      if (stopped) return;
      if (Date.now() > deadline) {
        stopped = true;
        clearInterval(timer);
        status.textContent =
          "Разбор занимает больше времени, чем обычно. Ссылка на этой странице ведёт к твоему результату.";
        const retry = document.createElement("a");
        retry.href = `/r/${encodeURIComponent(id)}`;
        retry.textContent = "Проверить ещё раз";
        job.querySelector(".waiting-actions").prepend(retry);
        return;
      }
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(`/r/${encodeURIComponent(id)}/status`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok && response.status !== 404) throw new Error("network");
        const data = await response.json();
        if (["ready", "failed", "not_found"].includes(data.status)) {
          stopped = true;
          clearInterval(timer);
          location.replace(`/r/${encodeURIComponent(id)}`);
          return;
        }
        status.textContent =
          "Рассчитываем карту и составляем персональный текст.";
        status.classList.remove("waiting-error");
      } catch {
        status.textContent =
          "Связь прервалась. Проверяем готовность ещё раз — повторно отправлять анкету не нужно.";
        status.classList.add("waiting-error");
      } finally {
        clearTimeout(timeout);
      }
      if (!stopped) setTimeout(poll, 4000);
    }
    poll();
  }

  const key = "matrica.saved-readings.v1";
  function saved() {
    try {
      const list = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(list)
        ? list
            .filter(
              (x) =>
                x &&
                typeof x.id === "string" &&
                /^[a-f0-9-]{32,36}$/.test(x.id) &&
                typeof x.title === "string",
            )
            .slice(0, 30)
        : [];
    } catch {
      return [];
    }
  }
  function write(list) {
    localStorage.setItem(key, JSON.stringify(list.slice(0, 30)));
  }
  const metaElement = document.getElementById("reading-meta");
  if (metaElement) {
    let meta;
    try {
      meta = JSON.parse(metaElement.textContent);
    } catch {
      return;
    }
    const status = document.getElementById("tool-status");
    const remember = document.getElementById("remember-reading");
    if (saved().some((x) => x.id === meta.id))
      remember.textContent = "Уже в моих разборах";
    remember.addEventListener("click", () => {
      try {
        write([
          {
            id: meta.id,
            title: meta.title.slice(0, 160),
            savedAt: new Date().toISOString(),
          },
          ...saved().filter((x) => x.id !== meta.id),
        ]);
        remember.textContent = "Сохранено";
        status.textContent =
          "Ссылка сохранена в «Мои разборы» на этом устройстве.";
      } catch {
        status.textContent =
          "Браузер не разрешил сохранение. Скопируй ссылку или скачай текст.";
      }
    });
    document
      .getElementById("print-reading")
      .addEventListener("click", () => window.print());
    document
      .getElementById("copy-reading")
      .addEventListener("click", async () => {
        const url = `${location.origin}/r/${encodeURIComponent(meta.id)}`;
        try {
          await navigator.clipboard.writeText(url);
          status.textContent =
            "Ссылка скопирована. Её получатель сможет прочитать этот разбор.";
        } catch {
          status.textContent = `Скопируй адрес: ${url}`;
        }
      });
  }
  const history = document.getElementById("history-list");
  if (history) {
    function render() {
      const items = saved();
      history.replaceChildren();
      document.getElementById("history-empty").hidden = items.length > 0;
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "history-item";
        const link = document.createElement("a");
        link.href = `/r/${encodeURIComponent(item.id)}`;
        const title = document.createElement("strong");
        title.textContent = item.title;
        const date = document.createElement("small");
        const parsed = new Date(item.savedAt);
        date.textContent = Number.isFinite(parsed.getTime())
          ? `Сохранён ${parsed.toLocaleDateString("ru-RU")}`
          : "Сохранённый разбор";
        link.append(title, date);
        const remove = document.createElement("button");
        remove.className = "history-forget";
        remove.type = "button";
        remove.textContent = "Убрать из списка";
        remove.addEventListener("click", () => {
          try {
            write(saved().filter((x) => x.id !== item.id));
            render();
          } catch {
            remove.textContent = "Не удалось убрать";
          }
        });
        row.append(link, remove);
        history.append(row);
      }
    }
    render();
    window.addEventListener("storage", (event) => {
      if (event.key === key) render();
    });
  }
  const form = document.getElementById("followup-form");
  if (form) {
    const question = document.getElementById("followup-question");
    const answer = document.getElementById("followup-answer");
    const status = document.getElementById("followup-status");
    document.querySelectorAll("[data-followup]").forEach((button) =>
      button.addEventListener("click", () => {
        question.value = button.dataset.followup;
        question.focus();
      }),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.dataset.busy === "true") return;
      form.dataset.busy = "true";
      const submit = form.querySelector("[type=submit]");
      submit.disabled = true;
      submit.textContent = "Готовим пояснение…";
      status.textContent =
        "Уточняем по твоему разбору. Это может занять несколько минут.";
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 240000);
      try {
        const response = await fetch(
          `/r/${encodeURIComponent(form.dataset.readingId)}/clarify`,
          {
            method: "POST",
            headers: { "X-Matrix-Action": "clarify" },
            body: new URLSearchParams({ question: question.value.trim() }),
            signal: controller.signal,
          },
        );
        const data = await response.json();
        if (!response.ok)
          throw new Error(
            data.error || "Не удалось получить ответ. Попробуй чуть позже.",
          );
        // Plain text avoids executing any markup emitted by the model.
        answer.textContent = data.answer;
        status.textContent = "Пояснение готово.";
      } catch (error) {
        status.textContent =
          error.name === "AbortError"
            ? "Ответ занял слишком много времени. Исходный разбор сохранён; попробуй позже."
            : error.message;
      } finally {
        clearTimeout(timeout);
        form.dataset.busy = "false";
        submit.disabled = false;
        submit.textContent = "Задать вопрос";
      }
    });
  }
})();
