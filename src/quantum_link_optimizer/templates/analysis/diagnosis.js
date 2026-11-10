"use strict";

const report = JSON.parse(document.getElementById("report-data").textContent);
const byId = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => value == null ? "нет данных" : Number(value).toFixed(digits);
const colors = {
  propagation: "#6d8fa3",
  serialization: "#c58b2b",
  icmp_processing: "#8a62b8",
  other_constant: "#3e6e8e",
  queue: "#bd4037",
};
const statuses = {
  likely: "вероятно",
  possible: "возможно",
  not_established: "не установлено",
};
const metrics = {
  base_ms: ["Базовая задержка", "ms"],
  queue_median_ms: ["Медиана очереди", "ms"],
  queue_share: ["Доля очереди в медиане", "fraction"],
  spearman_load_queue: ["Связь загрузки и очереди, Spearman", ""],
  peak_hour_utc: ["Час пика, UTC", ""],
  bottleneck_kbit_s: ["Полоса самого узкого участка", "kbit/s"],
  serialization_ms_largest: ["Сериализация самого большого пакета", "ms"],
  largest_bytes: ["Самый большой пакет", "bytes"],
  icmp_excess_ms: ["Добавка ICMP к UDP/TCP", "ms"],
  unexplained_constant_ms: ["Необъяснённая постоянная часть", "ms"],
  jitter_ms: ["Джиттер", "ms"],
  low_mode_ms: ["Нижний уровень", "ms"],
  high_mode_ms: ["Верхний уровень", "ms"],
  switches_per_hour: ["Переключений в час", ""],
  loss_rate: ["Доля потерь", "fraction"],
};
const units = { ms: " мс", fraction: "", "kbit/s": " кбит/с", bytes: " байт", "": "" };

function el(name, attributes = {}, children = []) {
  const node = document.createElement(name);

  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") {
      node.textContent = value;
    } else {
      node.setAttribute(key, value);
    }
  }

  node.append(...children);
  return node;
}

function svg(name, attributes = {}, text = null) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);

  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, value);
  }

  if (text !== null) {
    node.textContent = text;
  }

  return node;
}

function value(item) {
  if (item.metric === "level_steps") {
    return item.value
      .map((step) => `${new Date(step.time_s * 1000).toISOString().slice(11, 16)} UTC: ` +
        `${fmt(step.before_ms, 0)} → ${fmt(step.after_ms, 0)} мс`)
      .join(", ");
  }

  const [label, unit] = metrics[item.metric] || [item.metric, item.unit];

  if (unit === "fraction") {
    return `${label}: ${fmt(item.value * 100)}%`;
  }

  return `${label}: ${fmt(item.value, unit === "" ? 2 : 1)}${units[unit] ?? ""}`;
}

function panel(title, eyebrow, children) {
  return el("section", { class: "panel" }, [
    el("p", { class: "eyebrow", text: eyebrow }),
    el("h2", { text: title }),
    ...children,
  ]);
}

function verdictCards(diagnosis) {
  const verdict = diagnosis.verdict || {};
  const healthy = verdict.cause === "healthy";
  const where = verdict.where;
  const top = (diagnosis.hypotheses[verdict.scope?.[0] || "A"] || [])[0];

  return el("section", { class: "cards" }, [
    el("article", { class: `card ${healthy ? "fine" : "alert"}` }, [
      el("p", { text: "Вероятная причина" }),
      el("strong", { text: verdict.title || "нет данных" }),
      el("small", {
        text: verdict.score == null
          ? "Недостаточно измерений"
          : `Путь ${verdict.scope || "—"} · ${statuses[verdict.status] || "норма"} · оценка ${fmt(verdict.score, 2)}`,
      }),
    ]),
    el("article", { class: "card" }, [
      el("p", { text: "Где" }),
      el("strong", { text: where ? where.segment : "Участок не определён" }),
      el("small", {
        text: where
          ? where.basis
          : "Нужны пробы до промежуточных узлов, двух конечных точек мало",
      }),
    ]),
    el("article", { class: "card" }, [
      el("p", { text: "Что проверить" }),
      el("strong", { text: healthy ? "Ничего срочного" : "Следующий замер" }),
      el("small", { text: healthy ? "RTT в пределах цели" : (top?.next_test || "—") }),
    ]),
  ]);
}

function stackedBar(parts, scale) {
  const bar = el("div", { class: "bar" });

  for (const part of parts) {
    const share = scale ? part.ms / scale * 100 : 0;
    const segment = el("span", { text: share > 8 ? `${fmt(part.ms, 0)} мс` : "" });
    segment.style.width = `${share}%`;
    segment.style.background = colors[part.id] || "#999";
    bar.append(segment);
  }

  return bar;
}

function budgetBlock(path, summary) {
  const typical = summary.budget || [];
  const tail = typical.map((part) => part.id === "queue" ? { ...part, ms: summary.queue_p95_ms } : part);
  const scale = tail.reduce((sum, part) => sum + part.ms, 0);
  const legend = el("div", { class: "legend" });

  for (const part of typical) {
    const swatch = el("i");
    swatch.style.background = colors[part.id] || "#999";
    const text = part.id === "queue"
      ? `${part.label}: ${fmt(part.ms)} мс, в хвосте ${fmt(summary.queue_p95_ms)} мс`
      : `${part.label}: ${fmt(part.ms)} мс`;
    legend.append(el("span", {}, [swatch, text]));
  }

  return el("div", { class: "budget" }, [
    el("h3", { text: `Путь ${path} · медиана ${fmt(summary.median_ms)} мс · p95 ${fmt(summary.p95_ms)} мс` }),
    el("p", { class: "muted", text: "Обычная проба" }),
    stackedBar(typical, scale),
    el("p", { class: "muted", text: "Хвост: очередь на уровне p95" }),
    stackedBar(tail, scale),
    legend,
  ]);
}

function pathTable(paths) {
  const rows = [
    ["Цель измерения", (s) => `${s.target} · ${s.probe}${s.packet_bytes ? ` · ${s.packet_bytes} байт` : ""}`],
    ["Ответов / проб", (s) => `${s.answered} / ${s.probes}`],
    ["Потери", (s) => `${fmt((s.loss_rate || 0) * 100)}%`],
    ["Минимум", (s) => `${fmt(s.min_ms)} мс`],
    ["Медиана / p95 / p99", (s) => `${fmt(s.median_ms)} / ${fmt(s.p95_ms)} / ${fmt(s.p99_ms)} мс`],
    ["Базовая (скользящий минимум)", (s) => `${fmt(s.base_ms)} мс`],
    ["Очередь: медиана / p95", (s) => `${fmt(s.queue_median_ms)} / ${fmt(s.queue_p95_ms)} мс`],
    ["Выше цели", (s) => `${fmt((s.above_target_share || 0) * 100)}%`],
    ["Ступени базовой задержки", (s) => String(s.shape?.steps?.length ?? 0)],
    ["Суточный пик", (s) => s.shape?.daily ? `${s.shape.daily.peak_hour_utc}:00 UTC, ×${fmt(s.shape.daily.peak_to_quiet_ratio, 2)}` : "меньше суток данных"],
  ];
  const names = Object.keys(paths);
  const head = el("tr", {}, [el("th", { text: "Показатель" }), ...names.map((n) => el("th", { text: `Путь ${n}` }))]);
  const body = rows.map(([label, read]) =>
    el("tr", {}, [el("td", { text: label }), ...names.map((n) => el("td", { text: read(paths[n]) }))]));

  return el("div", { class: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)])]);
}

function chart(data, target) {
  const width = 1000;
  const height = 300;
  const pad = { left: 56, right: 16, top: 16, bottom: 30 };
  const series = Object.entries(data);
  const times = series.flatMap(([, s]) => s.time_s);
  const values = series.flatMap(([, s]) => s.rtt_ms);
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const sorted = [...values].sort((a, b) => a - b);
  const p99 = sorted[Math.floor(sorted.length * 0.99)] || target;
  const top = Math.max(target * 1.5, p99 * 1.1);
  const x = (t) => pad.left + (t - t0) / Math.max(t1 - t0, 1) * (width - pad.left - pad.right);
  const y = (v) => height - pad.bottom - Math.min(v, top) / top * (height - pad.top - pad.bottom);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "RTT и базовая задержка" });

  for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
    const v = top * tick;
    root.append(svg("line", { x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v), stroke: "#e3e9ec" }));
    root.append(svg("text", { x: 8, y: y(v) + 4, "font-size": 11, fill: "#5c7480" }, `${Math.round(v)} мс`));
  }

  root.append(svg("line", {
    x1: pad.left, x2: width - pad.right, y1: y(target), y2: y(target),
    stroke: "#bd4037", "stroke-dasharray": "6 5",
  }));

  for (const [path, s] of series) {
    const color = path === "A" ? "#2368b5" : "#7652af";

    for (let i = 0; i < s.time_s.length; i += 1) {
      root.append(svg("circle", { cx: x(s.time_s[i]), cy: y(s.rtt_ms[i]), r: 1.3, fill: color, opacity: 0.35 }));
    }

    const line = s.time_s.map((t, i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(s.base_ms[i]).toFixed(1)}`).join("");
    root.append(svg("path", { d: line, fill: "none", stroke: color, "stroke-width": 2 }));
  }

  const hours = (t1 - t0) / 3600;
  root.append(svg("text", { x: width - pad.right - 4, y: height - 8, "font-size": 11, fill: "#5c7480", "text-anchor": "end" }, `${fmt(hours)} ч`));
  return root;
}

function hypothesesBlock(hypotheses, verdicts) {
  const children = [];

  for (const [path, items] of Object.entries(hypotheses)) {
    const verdict = verdicts[path];
    children.push(el("h3", { text: `Путь ${path}: ${verdict?.title || "—"}` }));

    if (!items.length) {
      children.push(el("p", { class: "muted", text: "Признаков проблемы не найдено" }));
    }

    for (const item of items) {
      const score = el("span", { class: "score" }, [el("b")]);
      score.firstChild.style.width = `${item.score * 100}%`;
      children.push(el("div", { class: "hypothesis" }, [
        el("div", {}, [el("strong", { text: item.title }), score, el("span", { class: "muted", text: fmt(item.score, 2) })]),
        el("ul", {}, item.evidence.map((e) => el("li", { text: value(e) }))),
        el("p", { class: "muted", text: `Проверить: ${item.next_test}` }),
      ]));
    }
  }

  return children;
}

function segmentsBlock(route) {
  const rows = [];

  for (const [path, info] of Object.entries(route)) {
    for (const s of info.segments) {
      rows.push(el("tr", {}, [
        el("td", { text: path }),
        el("td", { text: `${s.from} → ${s.to}` }),
        el("td", { text: `${fmt(s.added_floor_ms)} мс` }),
        el("td", { text: `${fmt(s.added_variable_ms)} мс` }),
        el("td", { text: s.steps.length ? s.steps.map((x) => `${fmt(x.before_ms, 0)} → ${fmt(x.after_ms, 0)} мс`).join(", ") : "—" }),
      ]));
    }
  }

  const head = el("tr", {}, ["Путь", "Участок", "Добавка постоянной части", "Добавка переменной части", "Ступени"].map((t) => el("th", { text: t })));
  return el("div", { class: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)])]);
}

function single() {
  const d = report.decomposition;
  const diagnosis = report.diagnosis;
  const content = byId("content");
  byId("title").textContent = report.title || "Разбор RTT";
  byId("lead").textContent = `${report.probes} проб · источники: ${report.sources.join(", ")} · цель ${d.settings.target_ms} мс`;
  content.append(verdictCards(diagnosis));
  content.append(panel("Из чего складывается задержка", "01 · СОСТАВЛЯЮЩИЕ", [
    ...Object.entries(d.paths).filter(([, s]) => s.budget).map(([p, s]) => budgetBlock(p, s)),
    el("p", { class: "muted", text: "Распространение — оценка по расстоянию, остальное — по измерениям, части приблизительны" }),
    pathTable(d.paths),
  ]));

  if (Object.keys(d.chart).length) {
    content.append(panel("RTT во времени", "02 · ДИНАМИКА", [
      chart(d.chart, d.settings.target_ms),
      el("p", { class: "muted", text: "Точки — RTT, линия — базовая задержка, пунктир — цель" }),
    ]));
  }

  content.append(panel("Гипотезы и основания", "03 · ПРИЧИНЫ", hypothesesBlock(diagnosis.hypotheses, diagnosis.by_path)));

  if (Object.keys(report.segments).length) {
    content.append(panel("Участки пути", "04 · ЛОКАЛИЗАЦИЯ", [segmentsBlock(report.segments)]));
  }

  if (diagnosis.missing_tests.length) {
    content.append(panel("Каких измерений не хватает", "05 · ДАННЫЕ", [
      el("ul", {}, diagnosis.missing_tests.map((t) => el("li", { text: t }))),
    ]));
  }

  byId("scope").textContent = diagnosis.scope;
}

function examples() {
  byId("title").textContent = report.title;
  byId("lead").textContent = `Верно определено: ${report.matched} из ${report.total}`;
  const head = el("tr", {}, ["Пример", "Ожидалось", "Найдено", "Совпало", ""].map((t) => el("th", { text: t })));
  const rows = report.examples.map((row) => el("tr", {}, [
    el("td", { text: row.description }),
    el("td", { text: [row.expected.cause, row.expected.scope, row.expected.segment].filter(Boolean).join(" · ") }),
    el("td", { text: [row.found.title, row.found.scope, row.found.segment].filter(Boolean).join(" · ") }),
    el("td", { class: row.matched ? "ok" : "bad", text: row.matched ? "да" : "нет" }),
    el("td", {}, [el("a", { href: row.page, text: "разбор" })]),
  ]));
  byId("content").append(panel("Примеры с известной причиной", "ПРОВЕРКА ПРАВИЛ", [
    el("div", { class: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)])]),
  ]));
  byId("scope").textContent = report.scope;
}

if (report.kind === "rtt_diagnosis_examples") {
  examples();
} else {
  single();
}
