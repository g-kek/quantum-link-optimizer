"use strict";

const report = JSON.parse(document.getElementById("report-data").textContent);
const byId = (id) => document.getElementById(id);
const number = (value, digits = 2) => value == null ? "нет данных" : Number(value).toFixed(digits);
const labels = {
  normal: "Нормальный режим",
  swap: "Смена быстрой ветви",
  "common-700": "Оба пути около 700 мс",
  "slow-kms": "Медленная выдача ключей",
  "key-exhaustion": "Исчерпание запаса ключей",
  "shared-queue": "Общая очередь A/B",
  "receiver-restart": "Рестарт получателя",
  "loss-a": "Потери на A",
};
let timer = null;

function options(element, values, selected) {
  element.replaceChildren();

  for (const value of values) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = labels[value] || String(value);
    element.append(option);
  }

  if (values.map(String).includes(String(selected))) {
    element.value = String(selected);
  }
}

function selectedTrial() {
  return report.trials.find((trial) =>
    trial.case === byId("case").value &&
    String(trial.seed) === byId("seed").value &&
    trial.policy === byId("policy").value
  );
}

function stopPlayback() {
  clearInterval(timer);
  timer = null;
  byId("play").textContent = "Воспроизвести";
}

function refreshFilters() {
  stopPlayback();
  const candidates = report.trials.filter((trial) => trial.case === byId("case").value);
  const oldSeed = byId("seed").value;
  const oldPolicy = byId("policy").value || "ewma";
  options(byId("seed"), [...new Set(candidates.map((trial) => trial.seed))], oldSeed);
  options(byId("policy"), [...new Set(candidates.map((trial) => trial.policy))], oldPolicy);
  byId("time").value = "1000";
  drawTrial();
}

function svgNode(name, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);

  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }

  if (text !== undefined) {
    element.textContent = text;
  }

  return element;
}

const stageColors = {
  source_lag: "#9fb3bf",
  router_queue: "#bd4037",
  key_wait: "#c58b2b",
  send: "#5f8fa8",
  network: "#2368b5",
  receiver_queue: "#d4776f",
  receiver_key: "#e0b065",
  receiver_processing: "#7652af",
  ack: "#8aa0ab",
};
const lifecycles = { open: "открыт", unobserved: "нет данных", resolved: "закрыт" };
const units = { ms: "мс", ns: "нс", keys: "ключей", commands: "команд", probes: "проб", fraction: "", boolean: "", bytes: "байт" };

function schematic(location) {
  const root = svgNode("svg", { viewBox: "0 0 320 92", role: "img", "aria-label": "Где инцидент" });
  const focus = new Set(location?.component ? [location.component] : location?.candidates || []);
  const exact = Boolean(location?.component);
  const tone = (name) => focus.has(name) ? (exact ? "#bd4037" : "#d79a2b") : "#aebdc5";
  const box = (x, y, w, label, name) => {
    root.append(svgNode("rect", { x, y, width: w, height: 22, rx: 5, fill: "#fff", stroke: tone(name), "stroke-width": focus.has(name) ? 2.5 : 1.2 }));
    root.append(svgNode("text", { x: x + w / 2, y: y + 15, "text-anchor": "middle", "font-size": 10, fill: "#26394a" }, label));
  };
  const line = (d, name, label, ly) => {
    root.append(svgNode("path", { d, fill: "none", stroke: tone(name), "stroke-width": focus.has(name) ? 3 : 1.5 }));
    root.append(svgNode("text", { x: 160, y: ly, "text-anchor": "middle", "font-size": 10, fill: "#617782" }, label));
  };

  line("M70,58 C110,40 210,40 250,58", "segment:A", "A", 40);
  line("M70,70 C110,88 210,88 250,70", "segment:B", "B", 92);

  if (focus.has("shared")) {
    root.append(svgNode("rect", { x: 140, y: 52, width: 40, height: 24, rx: 4, fill: "#fff", stroke: tone("shared"), "stroke-width": 2.5 }));
    root.append(svgNode("text", { x: 160, y: 68, "text-anchor": "middle", "font-size": 9, fill: "#26394a" }, "общий"));
  }

  box(4, 53, 66, "Отправитель", "sender");
  box(250, 53, 66, "Получатель", "receiver");
  box(135, 4, 50, "KMS", "kms");
  root.append(svgNode("path", { d: "M135,15 L37,53 M185,15 L283,53", stroke: tone("kms"), "stroke-dasharray": "3 3", fill: "none" }));
  return root;
}

function measurementText(item) {
  const value = typeof item.value === "number" ? number(item.value, item.unit === "fraction" ? 2 : 1) : String(item.value);
  const unit = units[item.unit] ?? item.unit ?? "";
  return `${item.metric}: ${value} ${unit}`.trim() + (item.age_ms != null ? ` · возраст ${number(item.age_ms, 0)} мс` : "");
}

function incidentCards(trial, cutoff) {
  const latest = new Map();

  for (const event of trial.events) {
    if (event.kind === "incident" && event.t <= cutoff) {
      const first = latest.get(event.id)?.firstT ?? event.t;
      latest.set(event.id, { ...event, firstT: first });
    }
  }

  const items = [...latest.values()]
    .filter((item) => item.class !== "telemetry_gap")
    .sort((a, b) => (a.lifecycle === "open" ? 0 : 1) - (b.lifecycle === "open" ? 0 : 1) || a.firstT - b.firstT);
  const container = byId("incidents");
  container.replaceChildren();

  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Инцидентов нет";
    container.append(empty);
    return;
  }

  for (const item of items.slice(0, 6)) {
    const card = document.createElement("article");
    card.className = `incident ${item.lifecycle}`;
    const header = document.createElement("header");
    const title = document.createElement("h4");
    title.textContent = `${item.title} · ${item.scope}`;
    const chip = document.createElement("span");
    chip.className = `chip ${item.lifecycle}`;
    chip.textContent = lifecycles[item.lifecycle] || item.lifecycle;
    header.append(title, chip);
    card.append(header, schematic(item.location));
    const list = document.createElement("dl");
    const rows = [
      ["Где", item.location ? `${item.location.title}${item.location.ambiguous ? " (неоднозначно)" : ""}` : "не определено"],
      ["Замечен", `${number(item.firstT, 2)} с`],
      ["Основания", item.measurements.map(measurementText).join("; ") || "—"],
      ["Альтернативы", item.alternatives.join(", ") || "—"],
      ["Действие", item.action || "—"],
    ];

    for (const [term, text] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = term;
      const dd = document.createElement("dd");
      dd.textContent = text;
      list.append(dt, dd);
    }

    card.append(list);
    container.append(card);
  }

  if (items.length > 6) {
    const more = document.createElement("p");
    more.className = "muted";
    more.textContent = `И ещё ${items.length - 6}`;
    container.append(more);
  }
}

function drawStages(trial, cutoff) {
  const chart = byId("stages");
  const legend = byId("stages-legend");
  chart.replaceChildren();
  legend.replaceChildren();

  if (!trial.stages) {
    byId("stages-note").textContent = "Этапы для этого прогона не записаны";
    return;
  }

  const done = new Set(trial.events.filter((e) => e.kind === "command" && e.t <= cutoff).map((e) => e.id));
  const commands = trial.stages.commands;
  const top = Math.max(150, ...commands.map((c) => c.total_ms)) * 1.05;
  const width = 1000;
  const left = 56;
  const bar = (width - left - 10) / Math.max(commands.length, 1);
  const y = (value) => 215 - value / top * 195;

  for (const tick of [0, 0.5, 1]) {
    chart.append(svgNode("text", { x: 48, y: y(top * tick) + 4, "text-anchor": "end", "font-size": 10, fill: "#617782" }, number(top * tick, 0)));
  }

  chart.append(svgNode("line", { x1: left, x2: width - 10, y1: y(100), y2: y(100), stroke: "#c08b39", "stroke-dasharray": "6 5" }));

  commands.forEach((command, index) => {
    let base = 0;

    for (const [stage, value] of Object.entries(command.stages_ms)) {
      if (value <= 0) {
        continue;
      }

      const rect = svgNode("rect", {
        x: left + index * bar + 1,
        y: y(base + value),
        width: Math.max(bar - 2, 1),
        height: Math.max(y(base) - y(base + value), 0.5),
        fill: stageColors[stage] || "#999",
        opacity: done.has(command.id) ? 1 : 0.25,
      });
      rect.append(svgNode("title", {}, `${command.id}: ${trial.stages.labels[stage]} ${number(value)} мс`));
      chart.append(rect);
      base += value;
    }
  });

  for (const [stage, label] of Object.entries(trial.stages.labels)) {
    const item = document.createElement("span");
    item.style.color = stageColors[stage];
    item.textContent = `■ ${label}`;
    legend.append(item);
  }

  const tail = trial.stages.tail;
  const largest = [...tail.stages].sort((a, b) => b.mean_ms - a.mean_ms)[0];
  byId("stages-note").textContent =
    `Хвост от p95: в среднем ${number(tail.mean_total_ms, 0)} мс, больше всего — ${largest.label.toLowerCase()} ${number(largest.mean_ms, 0)} мс`;
}

function accuracyCards() {
  const data = report.diagnostics_accuracy;

  if (!data) {
    return;
  }

  const container = byId("accuracy");
  container.hidden = false;
  const cards = [
    ["Инциденты найдены", `${data.detected} из ${data.expected_incidents}`, `${data.trials} эпизодов, seeds ${data.seeds.join(", ")}`],
    ["Компонент назван верно", `${data.location_correct} из ${data.location_checked}`, "Там, где компонент известен по сценарию"],
    ["Ложные срабатывания", `${number(data.false_alarms_per_trial, 2)} на эпизод`, data.baseline_false_alarms_per_trial != null ? `Прежние правила: ${number(data.baseline_false_alarms_per_trial, 2)}` : `Медиана обнаружения ${number(data.time_to_detect_ms_median, 0)} мс`],
  ];

  for (const [label, value, note] of cards) {
    const article = document.createElement("article");
    const p = document.createElement("p");
    p.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    const small = document.createElement("small");
    small.textContent = note;
    article.append(p, strong, small);
    container.append(article);
  }
}

function drawTrial() {
  const trial = selectedTrial();

  if (!trial) {
    return;
  }

  const cutoff = trial.duration_s * Number(byId("time").value) / 1000;
  const chart = byId("chart");
  chart.replaceChildren();
  const allY = trial.events.map((event) => event.rtt_ms ?? event.latency_ms).filter((value) => value != null);
  const maxY = Math.max(150, ...allY) * 1.08;
  const x = (time) => 62 + time / Math.max(trial.duration_s, 0.01) * 908;
  const y = (value) => 285 - Math.min(value, maxY) / maxY * 250;

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = maxY * tick / 4;
    chart.append(svgNode("line", { x1: 62, x2: 970, y1: y(value), y2: y(value), stroke: "#e2e9ec" }));
    chart.append(svgNode("text", { x: 52, y: y(value) + 4, "text-anchor": "end", fill: "#617782", "font-size": 11 }, number(value, 0)));
    const time = trial.duration_s * tick / 4;
    chart.append(svgNode("text", { x: x(time), y: 310, "text-anchor": "middle", fill: "#617782", "font-size": 11 }, `${number(time, 1)} с`));
  }

  chart.append(svgNode("text", { x: 12, y: 20, fill: "#617782", "font-size": 11 }, "мс"));
  chart.append(svgNode("line", { x1: 62, x2: 970, y1: y(100), y2: y(100), stroke: "#c08b39", "stroke-dasharray": "6 5" }));

  for (const [path, color] of [["A", "#2368b5"], ["B", "#7652af"]]) {
    let d = "";
    let connected = false;

    for (const event of trial.events.filter((event) => event.kind === "probe" && event.path === path && event.t <= cutoff)) {
      if (event.rtt_ms == null) {
        connected = false;
        continue;
      }

      d += `${connected ? "L" : "M"}${x(event.t)},${y(event.rtt_ms)} `;
      connected = true;
    }

    chart.append(svgNode("path", { d, fill: "none", stroke: color, "stroke-width": 2 }));
  }

  for (const event of trial.events.filter((event) => event.kind === "command" && event.t <= cutoff)) {
    const px = x(event.t);
    const py = y(event.latency_ms);
    const point = svgNode("polygon", {
      points: `${px},${py - 4} ${px + 4},${py} ${px},${py + 4} ${px - 4},${py}`,
      fill: event.within_target ? "#087f65" : "#bd4037",
    });
    point.append(svgNode("title", {}, `${event.id}: ${event.status} · ${number(event.latency_ms)} мс · путь ${event.path || "не назначен"}`));
    chart.append(point);
  }

  chart.append(svgNode("line", { x1: x(cutoff), x2: x(cutoff), y1: 30, y2: 285, stroke: "#183747", "stroke-dasharray": "3 3" }));
  byId("time-label").textContent = `${number(cutoff, 2)} с`;
  byId("scenario-description").textContent = `${labels[trial.case] || trial.case} · seed ${trial.seed} · порог ACK 100 мс`;
  byId("trial-stats").replaceChildren();

  for (const text of [
    `В срок: ${trial.timely_executed} / ${trial.offered}`,
    `Неизвестный исход: ${trial.unknown_outcomes}`,
    `Байты копий: ${trial.extra_path_bytes}`,
    `Решение p99: ${number(trial.decision_us.p99)} мкс`,
  ]) {
    const element = document.createElement("span");
    element.textContent = text;
    byId("trial-stats").append(element);
  }

  const decisions = trial.events.filter((event) => event.kind === "decision" && event.t <= cutoff);
  const last = decisions.at(-1);
  const keyWait = trial.events.filter((event) => event.kind === "key_wait" && event.t <= cutoff).at(-1);
  const completed = trial.events.filter((event) => event.kind === "command" && event.t <= cutoff).at(-1);
  const inventory = trial.events.filter((event) => event.kind === "keys" && event.t <= cutoff).at(-1);
  byId("decision").textContent = last ? JSON.stringify(last, null, 2) : "Решение ещё не записано";
  byId("state").textContent = JSON.stringify({
    observed_inventory: inventory || null,
    local_queue_at_last_decision: last?.local_queue_depth ?? null,
    queue_and_pacing_ms_at_last_decision: last?.queue_and_pacing_ms ?? null,
    last_observed_sender_key_wait: keyWait || null,
    last_observed_command: completed || null,
  }, null, 2);
  byId("truth").textContent = JSON.stringify(trial.truth.filter((event) => event.t <= cutoff), null, 2);
  incidentCards(trial, cutoff);
  drawStages(trial, cutoff);
}

function tableRow(body, values) {
  const row = document.createElement("tr");

  for (const value of values) {
    const cell = document.createElement("td");
    cell.textContent = String(value);
    row.append(cell);
  }

  body.append(row);
}

byId("verdict").textContent = report.recommend_forecast ? "Требуется проверка внедрения" : "Оставить простые правила";
byId("episodes").textContent = String(report.trials.length);
byId("effect").textContent = `Прогноз − EWMA: ${number(report.paired.mean_difference * 100)} пп · интервал [${report.paired.interval_95.map((value) => number(value * 100)).join(" — ")}]`;
const totals = new Map();

for (const trial of report.trials) {
  const total = totals.get(trial.policy) || { offered: 0, timely: 0, unknown: 0, bytes: 0 };
  total.offered += trial.offered;
  total.timely += trial.timely_executed;
  total.unknown += trial.unknown_outcomes;
  total.bytes += trial.extra_path_bytes;
  totals.set(trial.policy, total);
}

for (const [policy, total] of totals) {
  tableRow(byId("comparison"), [policy, `${total.timely} / ${total.offered}`, total.unknown, total.bytes]);
}

for (const row of report.performance.trials) {
  tableRow(byId("performance"), [row.policy, row.logging_mode, `${row.timely_executed} / ${row.offered}`, number(row.decision_us.p99), number(row.observed_handling_lower_bound_ms.p99), number(row.resource_usage.process_cpu_s)]);
}

byId("sources").textContent = JSON.stringify(report.sources, null, 2);
accuracyCards();
options(byId("case"), [...new Set(report.trials.map((trial) => trial.case))], "swap");
byId("case").addEventListener("change", refreshFilters);
byId("seed").addEventListener("change", drawTrial);
byId("policy").addEventListener("change", drawTrial);
byId("time").addEventListener("input", drawTrial);
byId("play").addEventListener("click", () => {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
    byId("play").textContent = "Воспроизвести";
    return;
  }

  if (Number(byId("time").value) >= 1000) {
    byId("time").value = "0";
  }

  byId("play").textContent = "Пауза";
  timer = setInterval(() => {
    byId("time").value = String(Math.min(1000, Number(byId("time").value) + 5));
    drawTrial();

    if (Number(byId("time").value) >= 1000) {
      clearInterval(timer);
      timer = null;
      byId("play").textContent = "Воспроизвести";
    }
  }, 30);
});
byId("export").addEventListener("click", () => {
  const link = document.createElement("a");
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  link.href = url;
  link.download = "operator-demo-data.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
refreshFilters();
