/* Progressive enhancement: real POST form also works without JavaScript. */
(() => {
  "use strict";
  const form = document.getElementById("reading-form");
  if (!form) return;
  const topic = document.getElementById("analysis-type");
  const question = document.getElementById("question");
  const fields = document.getElementById("form-fields");
  const review = document.getElementById("form-review");
  const birthDate = document.getElementById("birth-date");
  const birthTime = document.getElementById("birth-time");
  const questions = {
    personality: [
      "Какие мои сильные стороны?",
      "Что мне важно для ощущения уверенности?",
      "Какие привычные реакции мне мешают?",
    ],
    relationships: [
      "Что мне нужно в отношениях?",
      "Какой партнёр мне подходит?",
      "Какие сценарии у меня повторяются?",
    ],
    work: [
      "Какие способности стоит развивать?",
      "Какой формат работы мне ближе?",
      "Какие привычки влияют на моё отношение к деньгам?",
    ],
    current_period: [
      "Какие темы выделяются до конца текущего месяца?",
      "На что обратить внимание в отношениях в этом месяце?",
      "На что обратить внимание в работе в этом месяце?",
    ],
  };
  let reviewing = false;
  let submitting = false;
  function updateQuestions() {
    question.replaceChildren(new Option("Общий разбор темы", ""));
    (questions[topic.value] || questions.personality).forEach((text) =>
      question.add(new Option(text, text)),
    );
  }
  topic.addEventListener("change", updateQuestions);
  document.querySelectorAll("[data-topic]").forEach((link) => {
    link.addEventListener("click", () => {
      if (reviewing) edit();
      topic.value = link.dataset.topic;
      updateQuestions();
    });
  });
  function precision() {
    return form.querySelector('[name="time_precision"]:checked').value;
  }
  function updateTime() {
    const value = precision();
    const unknown = value === "unknown";
    birthTime.disabled = unknown;
    birthTime.required = !unknown;
    document.getElementById("time-field").hidden = unknown;
    document.getElementById("time-hint").textContent = {
      exact:
        "Укажи время или выбери «Не знаю». Время и город нужны для расчёта асцендента и домов.",
      approx:
        "Укажи примерное время. Асцендент и дома могут измениться даже при небольшой разнице во времени.",
      unknown:
        "Продолжим по дате. Без времени часть показателей, в том числе асцендент и дома, недоступна.",
    }[value];
  }
  form
    .querySelectorAll('[name="time_precision"]')
    .forEach((input) => input.addEventListener("change", updateTime));
  updateTime();
  function validateDate() {
    const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(birthDate.value);
    let valid = false;
    if (match) {
      const [, day, month, year] = match.map(Number);
      const d = new Date(year, month - 1, day);
      valid =
        year >= 1900 &&
        d.getFullYear() === year &&
        d.getMonth() === month - 1 &&
        d.getDate() === day &&
        d <= new Date();
    }
    birthDate.setCustomValidity(
      valid
        ? ""
        : "Укажи существующую дату от 01.01.1900 до сегодняшнего дня в формате ДД.ММ.ГГГГ.",
    );
  }
  birthDate.addEventListener("input", () => birthDate.setCustomValidity(""));
  birthDate.addEventListener("blur", validateDate);
  function setProgress(second) {
    document.getElementById("step-one").classList.toggle("current", !second);
    document.getElementById("step-two").classList.toggle("current", second);
  }
  function edit() {
    reviewing = false;
    review.hidden = true;
    fields.hidden = false;
    setProgress(false);
    topic.focus({ preventScroll: true });
    form.scrollIntoView({ block: "start" });
  }
  document.getElementById("edit-button").addEventListener("click", edit);
  form.addEventListener("submit", (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }
    if (!reviewing) {
      event.preventDefault();
      validateDate();
      if (!form.reportValidity()) return;
      const values = new FormData(form);
      const data = [
        ["Тема", topic.selectedOptions[0].textContent],
        ["Вопрос", question.selectedOptions[0].textContent],
        ["Имя", values.get("name").trim()],
        ["Пол", values.get("gender") === "ж" ? "Женщина" : "Мужчина"],
        ["Дата рождения", values.get("birth_date")],
        [
          "Время",
          precision() === "unknown"
            ? "Неизвестно"
            : values.get("birth_time") +
              (precision() === "approx" ? " · примерно" : " · точно"),
        ],
        ["Город рождения", values.get("birth_place").trim() || "Не указан"],
      ];
      const list = document.getElementById("review-data");
      list.replaceChildren();
      data.forEach(([label, value]) => {
        const row = document.createElement("div");
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = label;
        dd.textContent = value;
        row.append(dt, dd);
        list.append(row);
      });
      fields.hidden = true;
      review.hidden = false;
      reviewing = true;
      setProgress(true);
      review.focus({ preventScroll: true });
      form.scrollIntoView({ block: "start" });
      return;
    }
    submitting = true;
    const button = document.getElementById("confirm-button");
    button.disabled = true;
    button.textContent = "Собираем твой разбор…";
    document.getElementById("edit-button").disabled = true;
  });
  // A browser may restore the disabled submit button when returning from a report.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    submitting = false;
    const button = document.getElementById("confirm-button");
    button.disabled = false;
    button.textContent = "Всё верно, получить разбор ↗";
    document.getElementById("edit-button").disabled = false;
  });
})();
