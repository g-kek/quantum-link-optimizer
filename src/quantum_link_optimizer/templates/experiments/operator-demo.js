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
    point.append(svgNode("title", {}, `${event.id}: ${event.status}; ${number(event.latency_ms)} мс; путь ${event.path || "не назначен"}`));
    chart.append(point);
  }

  chart.append(svgNode("line", { x1: x(cutoff), x2: x(cutoff), y1: 30, y2: 285, stroke: "#183747", "stroke-dasharray": "3 3" }));
  byId("time-label").textContent = `${number(cutoff, 2)} с`;
  byId("scenario-description").textContent = `${labels[trial.case] || trial.case}. Seed ${trial.seed}. Дедлайн оценки — 100 мс; timeout сохраняет и поздние исходы.`;
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
  byId("decision").textContent = last ? JSON.stringify(last, null, 2) : "До этого момента решение ещё не записано.";
  byId("state").textContent = JSON.stringify({
    observed_inventory: inventory || null,
    local_queue_at_last_decision: last?.local_queue_depth ?? null,
    queue_and_pacing_ms_at_last_decision: last?.queue_and_pacing_ms ?? null,
    last_observed_sender_key_wait: keyWait || null,
    last_observed_command: completed || null,
  }, null, 2);
  byId("truth").textContent = JSON.stringify(trial.truth.filter((event) => event.t <= cutoff), null, 2);
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
byId("effect").textContent = `Прогноз − EWMA: ${number(report.paired.mean_difference * 100)} п.п.; описательный интервал [${report.paired.interval_95.map((value) => number(value * 100)).join("; ")}].`;
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

for (const request of report.operator_requests) {
  const item = document.createElement("li");
  item.textContent = request;
  byId("operator").append(item);
}

byId("sources").textContent = JSON.stringify(report.sources, null, 2);
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
