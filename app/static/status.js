/* Публичная статус-страница. Данные — /api/public/status, обновление раз в минуту. */
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const flag = (cc) => (/^[A-Z]{2}$/.test(cc || "") && cc !== "XX"
  ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "🌐");
const dt = (ts, withTime = true) => new Date(ts * 1000).toLocaleString("ru-RU",
  withTime ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short" });
const hm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const pct = (v) => (v === null || v === undefined ? "—" : `${v >= 99.995 ? 100 : v.toFixed(2).replace(/\.?0+$/, "")}%`);
const dur = (s) => {
  const m = Math.max(1, Math.round(s / 60));
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} ч ${m % 60 ? `${m % 60} мин` : ""}` : `${Math.floor(h / 24)} д ${h % 24} ч`;
};

const OVERALL = {
  up: ["ok", "Все серверы работают"],
  partial: ["warn", "Часть серверов недоступна"],
  major: ["bad", "Серьёзный сбой"],
};
const STATE = { up: ["ok", "работает"], down: ["bad", "недоступен"], maintenance: ["off", "обслуживание"] };
const LOAD = { low: "свободно", medium: "средняя нагрузка", high: "высокая нагрузка" };
const barTone = (v) => (v === null ? "none" : v >= 99.9 ? "ok" : v >= 90 ? "warn" : "bad");

function render(d) {
  document.title = d.title;
  $("#title").textContent = d.title;
  const [tone, text] = OVERALL[d.overall];
  const down = d.nodes.filter((n) => n.state === "down").length;
  return `
    <section class="overall ${tone}"><span class="dot"></span><div><b>${text}</b>
      <span>${down ? `недоступно ${down} из ${d.nodes.filter((n) => n.state !== "maintenance").length}` : `проверено в ${hm(d.updated)}`}</span></div></section>
    ${d.announce ? `<section class="announce ${esc(d.announce.level)}">${esc(d.announce.text).replace(/\n/g, "<br>")}</section>` : ""}
    <section class="card">
      ${d.nodes.length ? d.nodes.map((n, i) => node(n, i, d)).join("") : `<p class="muted center">Серверов для показа нет.</p>`}
      <div class="legend muted"><span>${dt(d.start, false)}</span><span>7 дней</span><span>сейчас</span></div>
    </section>
    ${d.incidents ? `<section class="card"><h2>Сбои за неделю</h2>${d.incidents.length ? d.incidents.map((x) => `
      <div class="inc ${x.end ? "" : "live"}"><span class="dot"></span><div>
        <b>${esc(x.node)}</b> — ${x.end ? `недоступен ${dur(x.end - x.start)}` : `недоступен уже ${dur(d.updated - x.start)}`}
        <span class="muted">${dt(x.start)}${x.end ? ` – ${hm(x.end)}` : " · продолжается"}</span></div></div>`).join("")
      : `<p class="muted">Сбоев не было.</p>`}</section>` : ""}`;
}

function node(n, i, d) {
  const [tone, label] = STATE[n.state];
  return `<div class="node">
    <div class="node-top">
      <span class="flag" aria-hidden="true">${flag(n.country)}</span>
      <b class="name">${esc(n.name)}</b>
      ${n.load ? `<span class="load ${n.load}" title="Нагрузка относительно обычной для этого сервера"><i></i><i></i><i></i>${LOAD[n.load]}</span>` : ""}
      <span class="tag ${tone}">${label}</span>
    </div>
    <div class="bars" data-i="${i}" role="img" aria-label="Доступность за 7 дней: ${pct(n.uptime_7d)}">
      ${n.bars.map((v, b) => `<i class="${barTone(v)}" data-b="${b}"></i>`).join("")}
    </div>
    <div class="node-foot muted"><span class="tip" id="tip-${i}"></span>
      <span>24 ч: <b>${pct(n.uptime_24h)}</b> · 7 дней: <b>${pct(n.uptime_7d)}</b></span></div>
  </div>`;
}

let data = null;
function bindBars() {
  // подсказка по делению: наведение на десктопе, тап на телефоне
  const show = (e) => {
    const b = e.target.closest(".bars i"); if (!b) return;
    const i = b.parentElement.dataset.i, k = Number(b.dataset.b), v = data.nodes[i].bars[k];
    const from = data.start + k * data.bucket;
    $(`#tip-${i}`).textContent = `${dt(from)}–${hm(Math.min(from + data.bucket, data.updated))} · ${v === null ? "нет данных" : `${pct(v)}`}`;
  };
  document.querySelectorAll(".bars").forEach((el) => {
    el.addEventListener("pointerover", show);
    el.addEventListener("click", show);
    el.addEventListener("pointerleave", () => { $(`#tip-${el.dataset.i}`).textContent = ""; });
  });
}

async function load() {
  try {
    const r = await fetch("/api/public/status", { cache: "no-store" });
    if (r.status === 404) { $("#app").innerHTML = `<p class="muted center">Статус-страница выключена.</p>`; return; }
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
    $("#app").innerHTML = render(data);
    bindBars();
    $("#foot").textContent = data.fresh ? "Обновляется автоматически раз в минуту"
      : "Нет связи с панелью управления — показаны последние известные данные";
  } catch {
    $("#foot").textContent = "Не удалось обновить данные, попробую снова через минуту";
  }
}
load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
