"use strict";

const report = JSON.parse(document.getElementById("report-data").textContent);
const byId = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => value == null ? "—" : Number(value).toFixed(digits);
const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(0)}%`;
const episodes = {
  load: "Рост нагрузки",
  degradation: "Постепенная деградация",
  keys: "Исчерпание ключей",
  sudden: "Внезапная ступень",
  background: "Фон без эпизода",
};

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

function panel(title, eyebrow, children) {
  return el("section", { class: "panel" }, [
    el("p", { class: "eyebrow", text: eyebrow }),
    el("h2", { text: title }),
    ...children,
  ]);
}

function table(head, rows) {
  return el("div", { class: "table-wrap" }, [
    el("table", {}, [
      el("thead", {}, [el("tr", {}, head.map((t) => el("th", { text: t })))]),
      el("tbody", {}, rows.map((cells) => el("tr", {}, cells.map((c) =>
        typeof c === "string" ? el("td", { text: c }) : el("td", {}, [c]))))),
    ]),
  ]);
}

function merged(warnings) {
  const total = { onsets: 0, warned: 0, leads: [], falsePerDay: 0, byEpisode: {} };

  for (const item of Object.values(warnings)) {
    total.onsets += item.onsets;
    total.warned += item.warned;
    total.falsePerDay += item.false_alarms_per_day || 0;

    if (item.median_lead_s != null) {
      total.leads.push(item.median_lead_s);
    }

    for (const [kind, value] of Object.entries(item.by_episode)) {
      const slot = total.byEpisode[kind] || { onsets: 0, warned: 0, leads: [] };
      slot.onsets += value.onsets;
      slot.warned += value.warned;

      if (value.median_lead_s != null) {
        slot.leads.push(value.median_lead_s);
      }

      total.byEpisode[kind] = slot;
    }
  }

  return total;
}

function mean(values) {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function chart(data) {
  const width = 1000;
  const height = 320;
  const strip = 50;
  const pad = { left: 56, right: 14, top: 14, bottom: 24 };
  const plot = height - strip - pad.top - pad.bottom;
  const points = data.time_s.map((t, i) => ({
    t, actual: data.actual_ms[i], low: data.low_ms[i], median: data.median_ms[i],
    high: data.high_ms[i], p: data.probability[i],
  })).filter((p) => p.t != null);
  const values = points.flatMap((p) => [p.actual, p.high]).filter((v) => v != null).sort((a, b) => a - b);
  const top = Math.max(report.target_ms * 1.5, (values[Math.floor(values.length * 0.99)] || 0) * 1.05);
  const t0 = points[0].t;
  const t1 = points[points.length - 1].t;
  const x = (t) => pad.left + (t - t0) / Math.max(t1 - t0, 1) * (width - pad.left - pad.right);
  const y = (v) => pad.top + plot - Math.min(v, top) / top * plot;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Прогноз RTT" });

  for (const tick of [0, 0.5, 1]) {
    root.append(svg("text", { x: 6, y: y(top * tick) + 4, "font-size": 11, fill: "#5c7480" }, `${Math.round(top * tick)} мс`));
    root.append(svg("line", { x1: pad.left, x2: width - pad.right, y1: y(top * tick), y2: y(top * tick), stroke: "#e3e9ec" }));
  }

  const upper = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.high).toFixed(1)}`).join("");
  const lower = [...points].reverse().map((p) => `L${x(p.t).toFixed(1)},${y(p.low).toFixed(1)}`).join("");
  root.append(svg("path", { d: `${upper}${lower}Z`, fill: "#2368b5", opacity: 0.16 }));
  root.append(svg("path", {
    d: points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.median).toFixed(1)}`).join(""),
    fill: "none", stroke: "#2368b5", "stroke-width": 1.5,
  }));

  for (const p of points) {
    if (p.actual != null) {
      root.append(svg("circle", { cx: x(p.t), cy: y(p.actual), r: 1.2, fill: p.actual > report.target_ms ? "#bd4037" : "#26394a", opacity: 0.55 }));
    }
  }

  root.append(svg("line", {
    x1: pad.left, x2: width - pad.right, y1: y(report.target_ms), y2: y(report.target_ms),
    stroke: "#bd4037", "stroke-dasharray": "6 5",
  }));
  const base = height - pad.bottom;

  for (const p of points) {
    const shade = Math.round(255 - (p.p || 0) * 190);
    root.append(svg("rect", {
      x: x(p.t), y: base - strip + 8, width: Math.max(1, (width - pad.left - pad.right) / points.length + 0.5),
      height: strip - 12, fill: `rgb(255,${shade},${shade})`,
    }));
  }

  root.append(svg("text", { x: 6, y: base - strip / 2 + 6, "font-size": 11, fill: "#5c7480" }, "P(выше)"));
  return root;
}

function main() {
  const results = report.results;
  const horizons = report.horizons_s;
  byId("title").textContent = report.title;
  byId("lead").textContent = report.source.kind === "synthetic"
    ? `Синтетическая неделя, шаг ${report.step_s} с, цель ${report.target_ms} мс`
    : `CSV ${report.source.file}, шаг ${report.step_s} с, цель ${report.target_ms} мс`;
  const content = byId("content");
  const model = results.model.horizons;
  const mid = model[Math.min(1, model.length - 1)];
  const warn = merged(mid.warnings);

  content.append(el("section", { class: "cards" }, [
    el("article", { class: "card" }, [
      el("p", { text: `Интервал на ${fmt(mid.horizon_s / 60, 0)} мин вперёд` }),
      el("strong", { text: `${pct(mid.band.coverage_80)} попаданий` }),
      el("small", { text: `Заявлено 80%, медианная ширина ${fmt(mid.band.band_width_ms)} мс` }),
    ]),
    el("article", { class: "card" }, [
      el("p", { text: "Предупреждено превышений" }),
      el("strong", { text: `${warn.warned} из ${warn.onsets}` }),
      el("small", { text: `Медианное упреждение ${fmt(mean(warn.leads) / 60)} мин` }),
    ]),
    el("article", { class: "card" }, [
      el("p", { text: "Ложные тревоги" }),
      el("strong", { text: `${fmt(warn.falsePerDay)} в сутки` }),
      el("small", { text: "Тревога без превышения в пределах горизонта" }),
    ]),
  ]));

  content.append(panel(`Прогноз на ${fmt(report.chart.horizon_s / 60, 0)} мин, путь ${report.chart.path}`, "01 · ПОСЛЕДНИЕ СУТКИ ПРОВЕРКИ", [
    chart(report.chart),
    el("div", { class: "band-legend" }, [
      el("span", { text: "Полоса — интервал p10–p90" }),
      el("span", { text: "Линия — медиана прогноза" }),
      el("span", { text: "Точки — фактический RTT" }),
      el("span", { text: "Нижняя полоса — вероятность превышения" }),
    ]),
  ]));

  const names = Object.keys(results);
  const rows = [];

  for (const [index, h] of horizons.entries()) {
    const best = Math.min(...names.map((n) => results[n].horizons[index].breach.brier ?? 9));

    for (const name of names) {
      const item = results[name].horizons[index];
      const w = merged(item.warnings);
      const brier = el("span", { class: item.breach.brier === best ? "good" : "", text: fmt(item.breach.brier, 3) });
      rows.push([
        `${fmt(h / 60, 0)} мин`, results[name].title, `${fmt(item.band.median_abs_error_ms)} мс`,
        pct(item.band.coverage_80), brier, fmt(item.breach.auc, 2),
        `${w.warned} / ${w.onsets}`, `${fmt(mean(w.leads) / 60)} мин`, fmt(w.falsePerDay),
      ]);
    }
  }

  content.append(panel("Модель и простые правила", "02 · СРАВНЕНИЕ", [
    table(["Горизонт", "Метод", "Ошибка медианы", "Покрытие 80%", "Brier", "AUC", "Предупреждено", "Упреждение", "Ложных в сутки"], rows),
    el("p", { class: "muted", text: "Все методы получили одинаковую конформную поправку интервала и калибровку вероятности на validation" }),
  ]));

  const kinds = Object.entries(warn.byEpisode).map(([kind, v]) => [
    episodes[kind] || kind, `${v.warned} / ${v.onsets}`, v.leads.length ? `${fmt(mean(v.leads) / 60)} мин` : "—",
  ]);
  content.append(panel("Что удаётся предвидеть", "03 · ПО ПРИЧИНАМ", [
    table(["Причина превышения", "Предупреждено", "Упреждение"], kinds),
    el("p", { class: "muted", text: "Внезапные ступени не имеют предвестников, их можно только быстро обнаружить" }),
  ]));

  content.append(panel("Определения", "04 · МЕТОДИКА", [
    el("ul", {}, Object.values(report.definitions).map((t) => el("li", { text: t }))),
  ]));
  byId("scope").textContent = report.scope;
}

main();
