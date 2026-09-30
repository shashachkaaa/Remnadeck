/* RemnaDeck — клиентская часть без сборки */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtBytes = (b) => {
  b = Number(b) || 0;
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const fmtNum = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("ru-RU"));
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const ago = (v) => {
  if (!v) return "никогда";
  const t = typeof v === "number" ? v * 1000 : Date.parse(v);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return `${Math.round(s / 86400)} дн назад`;
};
const dayLabel = (ts) => {
  const d = new Date(ts * 1000), today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Сегодня";
  if (d.toDateString() === y.toDateString()) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
};
const LEVEL = { bad: "сбой", warn: "внимание", ok: "норма", info: "действие" };

const ICONS = {
  box: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  server: '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01"/>',
  pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6H21"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c.8-3.4 3.2-5 6-5s5.2 1.6 6 5M16 5a3.5 3.5 0 010 7M18 15c1.6.6 2.6 2.2 3 5"/>',
  bell: '<path d="M6 16V11a6 6 0 0112 0v5l1.5 2h-15zM10 20.5a2 2 0 004 0"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4"/>',
  send: '<path d="M21 3L10 14M21 3l-7 18-4-7-7-4z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3M14 9l2 2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>',
  refresh: '<path d="M20 11a8 8 0 00-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0014.5 4.5L20 16M20 20v-4h-4"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3A4 4 0 0013 5.4l-1.5 1.5M14 10a4 4 0 00-5.7 0l-3 3A4 4 0 0011 18.6l1.5-1.5"/>',
  cloud: '<path d="M7 18h10a3.5 3.5 0 00.5-7 5.5 5.5 0 00-10.6-1A3.6 3.6 0 007 18z"/>',
  ssh: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  squad: '<path d="M8 11a3 3 0 100-6 3 3 0 000 6zM2 19a6 6 0 0112 0M17 11a3 3 0 100-6M16 19h6a5 5 0 00-4-4.9"/>',
  edit: '<path d="M4 20h4L19 9a2.8 2.8 0 00-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  power: '<path d="M12 3v9"/><path d="M6.3 7.3a8 8 0 1011.4 0"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  gauge: '<path d="M4.5 17a8.5 8.5 0 1115 0"/><path d="M12 13l4-4"/><circle cx="12" cy="13" r="1.4"/>',
  term: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>',
  logs: '<path d="M8 6h12M8 12h12M8 18h8M4 6h.01M4 12h.01M4 18h.01"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6l8-3z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  down: '<path d="M12 4v16M6 14l6 6 6-6"/>',
  up: '<path d="M12 20V4M6 10l6-6 6 6"/>',
  ram: '<rect x="3" y="7" width="18" height="10" rx="1.5"/><path d="M7 17v2M11 17v2M15 17v2M7 10v4M11 10v4M15 10v4"/>',
  table: '<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M3.5 10h17M10 10v10"/>',
  bolt: '<path d="M13 3L5 13h6l-1 8 8-10h-6l1-8z"/>',
  unlink: '<path d="M9.5 14.5l5-5M8 12l-2 2a3.5 3.5 0 005 5l2-2M16 12l2-2a3.5 3.5 0 00-5-5l-2 2M4 4l16 16"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  reset: '<path d="M4 12a8 8 0 108-8 8.5 8.5 0 00-6 2.5L4 8.5M4 4v4.5h4.5"/>',
  install: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 7v6M9.5 10.5L12 13l2.5-2.5M8 20h8"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/>',
  tool: '<path d="M14.5 6a3.5 3.5 0 004.7 4.6L21 12.4 12.4 21l-1.8-1.8A3.5 3.5 0 006 14.5L4 12.6 12.6 4z"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;

/* ---------- сеть ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !opts.noRedirect) { stopApp(); renderLogin(); throw new Error("Нужен вход"); }
  if (!r.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `Ошибка ${r.status}`);
  }
  return data;
}
const submitBtn = (form, e) => e.submitter || form.querySelector("button[type=submit]");
const post = (path, body, extra = {}) => api(path, { method: "POST", body: JSON.stringify(body || {}), ...extra });

function toast(msg, err = false) {
  const t = $("#toast");
  t.innerHTML = `<span class="dot ${err ? "bad" : "ok"}"></span>${esc(msg)}`;
  t.className = `toast show${err ? " err" : ""}`;
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = "toast"), 3400);
}

async function busy(btn, fn) {
  if (!btn) return fn();                       // вызов без кнопки — просто выполняем
  btn.disabled = true; btn.classList.add("loading");
  try { return await fn(); } finally { btn.disabled = false; btn.classList.remove("loading"); }
}

function countUp(root) {
  $$("[data-count]", root).forEach((el) => {
    const to = Number(el.dataset.count);
    if (!Number.isFinite(to) || to === 0) return;
    const t0 = performance.now(), dur = 850;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(to * e).toLocaleString("ru-RU");
      if (k < 1) requestAnimationFrame(step);
    };
    el.textContent = "0";
    requestAnimationFrame(step);
  });
}

/* ---------- вход и первичная настройка ---------- */
function authCard(inner) {
  $("#shell").hidden = true;
  const a = $("#auth"); a.hidden = false;
  a.innerHTML = `<div class="auth-card"><div class="brand"><img class="mark" src="/static/logo.svg" alt="" width="30" height="30"><span class="wordmark">Remna<b>Deck</b></span></div>${inner}</div>`;
}

function renderLogin() {
  authCard(`<h2>Вход</h2><p>Панель мониторинга Remnawave</p>
    <form class="stack" id="login-form">
      <label>Логин<input name="username" autocomplete="username" required autofocus></label>
      <label>Пароль<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="form-error" id="err"></p>
      <button class="btn btn-primary" type="submit">Войти</button>
    </form>`);
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    $("#err").textContent = "";
    await busy(e.submitter || $("button", e.target), async () => {
      try { await post("/api/login", { username: f.get("username"), password: f.get("password") }, { noRedirect: true }); startApp(); }
      catch (err) { $("#err").textContent = err.message; }
    });
  });
}

const wiz = { step: 0, data: { remnawave_url: "http://remnawave:3000" } };
const codeHdr = () => ({ headers: { "X-Setup-Code": wiz.data.code || "" }, noRedirect: true });

function renderSetup(dir = "fwd") {
  const d = wiz.data;
  const steps = [
    {
      title: "Администратор", text: "Код первого входа выдал install.sh. Он же лежит в .env (SETUP_TOKEN).",
      body: `<label>Код первого входа<input name="code" value="${esc(d.code)}" required autocomplete="one-time-code" autofocus></label>
        <label>Логин администратора<input name="username" value="${esc(d.username)}" required minlength="3" maxlength="32" autocomplete="username"></label>
        <div class="form-grid">
          <label>Пароль<input name="password" type="password" required minlength="8" autocomplete="new-password"></label>
          <label>Ещё раз<input name="password2" type="password" required minlength="8" autocomplete="new-password"></label>
        </div><span class="hint">Минимум 8 символов. В .env сохранится только хеш.</span>`,
      next: "Дальше",
    },
    {
      title: "Подключение к Remnawave", text: "Если панель на этом же сервере, адрес по умолчанию подойдёт.",
      body: `<label>Адрес API<input name="remnawave_url" value="${esc(d.remnawave_url)}" required></label>
        <label>API-токен<input name="remnawave_token" value="${esc(d.remnawave_token)}" placeholder="Remnawave → Настройки → API-токены"></label>
        <div class="check-line" id="check"></div>
        <button class="btn btn-sm" type="button" id="test-rw">Проверить подключение</button>`,
      next: "Дальше", skip: true,
    },
    {
      title: "Уведомления в Telegram", text: "Сюда придут алерты: нода отвалилась, путь сломался, сертификат истекает. Можно пропустить.",
      body: `<label>Токен бота<input name="tg_bot_token" value="${esc(d.tg_bot_token)}" placeholder="123456:ABC…"></label>
        <label>ID чата<input name="tg_chat_id" value="${esc(d.tg_chat_id)}" placeholder="например 123456789"></label>
        <div class="check-line" id="check"></div>
        <button class="btn btn-sm" type="button" id="test-tg">Отправить тестовое сообщение</button>`,
      next: "Завершить настройку",
    },
  ];
  const s = steps[wiz.step];
  authCard(`<div class="steps">${steps.map((_, i) => `<span class="${i <= wiz.step ? "done" : ""}"></span>`).join("")}</div>
    <div class="step-pane ${dir === "back" ? "back" : ""}">
      <h2>${s.title}</h2><p>${s.text}</p>
      <form class="stack" id="wiz" novalidate>${s.body}
        <p class="form-error" id="err"></p>
        <div class="form-foot">
          ${wiz.step ? `<button class="btn btn-ghost" type="button" id="back">Назад</button>` : "<span></span>"}
          <span style="display:flex;gap:8px">${s.skip ? `<button class="btn btn-ghost" type="button" id="skip">Настрою позже</button>` : ""}
          <button class="btn btn-primary" type="submit">${s.next}</button></span>
        </div>
      </form></div>`);

  const form = $("#wiz");
  const collect = () => Object.assign(wiz.data, Object.fromEntries(new FormData(form)));
  const err = (m) => ($("#err").textContent = m || "");
  const check = (ok, text) => ($("#check").innerHTML = `<span class="dot ${ok ? "ok" : "bad"}"></span>${esc(text)}`);

  $("#back")?.addEventListener("click", () => { collect(); wiz.step--; renderSetup("back"); });
  $("#skip")?.addEventListener("click", () => { collect(); wiz.data.remnawave_token = ""; wiz.step++; renderSetup(); });
  $("#test-rw")?.addEventListener("click", (e) => busy(e.target, async () => {
    collect();
    try {
      const r = await post("/api/settings/test-remnawave", { remnawave_url: wiz.data.remnawave_url, remnawave_token: wiz.data.remnawave_token }, codeHdr());
      check(true, `Работает: ${r.nodes} нод, ${r.latency_ms} мс`);
    } catch (x) { check(false, x.message); }
  }));
  $("#test-tg")?.addEventListener("click", (e) => busy(e.target, async () => {
    collect();
    try { await post("/api/settings/test-telegram", { tg_bot_token: wiz.data.tg_bot_token, tg_chat_id: wiz.data.tg_chat_id }, codeHdr()); check(true, "Сообщение отправлено — проверь Telegram"); }
    catch (x) { check(false, x.message); }
  }));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    collect(); err("");
    busy(e.submitter || $("button[type=submit]", form), async () => {
      try {
        if (wiz.step === 0) {
          if (!d.code) return err("Введи код первого входа");
          if ((d.username || "").trim().length < 3) return err("Логин — минимум 3 символа");
          if ((d.password || "").length < 8) return err("Пароль — минимум 8 символов");
          if (d.password !== d.password2) return err("Пароли не совпадают");
          await post("/api/setup/check", { code: d.code }, { noRedirect: true });
        }
        if (wiz.step < steps.length - 1) { wiz.step++; renderSetup(); return; }
        const { password2, ...payload } = wiz.data;
        await post("/api/setup", payload, { noRedirect: true });
        toast("Панель настроена");
        startApp();
      } catch (x) { err(x.message); }
    });
  });
}

/* ---------- оболочка ---------- */
let refreshTimer = null, statusTimer = null, navToken = 0;

function stopApp() { clearInterval(refreshTimer); clearInterval(statusTimer); }

function startApp() {
  $("#auth").hidden = true; $("#shell").hidden = false;
  $$("#nav a").forEach((a) => { if (!a.querySelector("svg")) a.insertAdjacentHTML("afterbegin", icon(a.dataset.icon)); });
  loadStatus();
  clearInterval(statusTimer); statusTimer = setInterval(() => { if (!document.hidden) loadStatus(); }, 30000);
  loadVersion();
  if (!appbarReady) { appbarReady = true; initAppbar(); }
  route();
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("#me-name").textContent = s.user;
    $("#avatar").textContent = (s.user || "?").slice(0, 1).toUpperCase();
    $("#b-nodes").textContent = s.nodes ?? "";
    $("#b-events").textContent = s.incidents_24h || "";
    // «Bedolaga» в меню — только если бот или кабинет найдены на этом сервере
    const bd = $("#nav-bedolaga");
    if (bd && bd.hidden === !!s.bedolaga) { bd.hidden = !s.bedolaga; movePill(); }
    $("#conn").innerHTML = s.remnawave.ok
      ? `<span class="dot ok live"></span><span>Remnawave <b>${s.remnawave.latency_ms} мс</b></span>`
      : `<span class="dot bad"></span><span>Remnawave <b>нет связи</b></span>`;
    $("#conn").title = s.remnawave.error || "";
    paintAppbar(s);
  } catch { /* покажет view */ }
}

/* ---------- закреплённая шапка ---------- */
let jobsPoll = null, appbarReady = false;
function paintAppbar(s) {
  const conn = $("#ab-conn");
  if (conn) {
    conn.classList.toggle("bad", !s.remnawave.ok);
    $(".dot", conn).className = `dot ${s.remnawave.ok ? "ok live" : "bad"}`;
    $("b", conn).textContent = s.remnawave.ok ? `${s.remnawave.latency_ms} мс` : "нет связи";
    conn.title = s.remnawave.ok ? "Связь с Remnawave" : `Remnawave: ${s.remnawave.error || "нет связи"}`;
  }
  const ev = $("#ab-ev-n");
  if (ev) { ev.textContent = s.incidents_24h > 99 ? "99+" : s.incidents_24h || ""; ev.hidden = !s.incidents_24h; }
  const jobs = $("#ab-jobs");
  if (jobs) {
    jobs.hidden = !s.jobs_running;
    $("#ab-jobs-n").textContent = s.jobs_running > 1 ? s.jobs_running : "";
    jobs.title = s.jobs_running ? `Идёт: ${s.job_title}` : "";
    jobs.dataset.job = s.job_last || "";
    jobs.dataset.title = s.job_title || "";
  }
  // пока идут задачи — следим чаще, чтобы значок погас сразу по окончании
  clearTimeout(jobsPoll);
  if (s.jobs_running) jobsPoll = setTimeout(loadStatus, 4000);
}

function initAppbar() {
  $("#version")?.addEventListener("click", versionSheet);
  $$("[data-ab-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.abIcon); });
  $("#ab-refresh")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    e.currentTarget.classList.add("turning");
    await redraw(false);
    await loadStatus();
    setTimeout(() => e.currentTarget.classList.remove("turning"), 500);
  }));
  // значок задачи: сразу открываем её лог, а не список
  $("#ab-jobs")?.addEventListener("click", (e) => {
    const j = e.currentTarget.dataset.job;
    if (j && typeof jobConsole === "function") { e.preventDefault(); jobConsole(j, e.currentTarget.dataset.title); }
  });
  const bar = $("#appbar");
  const onScroll = () => {
    bar.classList.toggle("scrolled", window.scrollY > 4);
    // название раздела в шапке — когда крупный заголовок уехал или его нет вовсе
    const top = $(".top");
    const hidden = top?.classList.contains("bare") || !top || top.getBoundingClientRect().bottom < bar.offsetHeight;
    bar.classList.toggle("titled", !!hidden);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

function movePill() {
  const a = $("#nav a.active"), pill = $("#nav-pill");
  if (!a) { pill.style.opacity = 0; return; }
  pill.style.opacity = 1;
  pill.style.height = `${a.offsetHeight}px`;
  pill.style.transform = `translateY(${a.offsetTop}px)`;
}
window.addEventListener("resize", movePill);

const setMenu = (open) => $("#shell").classList.toggle("menu-open", open);
$("#burger").addEventListener("click", () => setMenu(true));
$("#side-close").addEventListener("click", () => setMenu(false));
$("#scrim").addEventListener("click", () => setMenu(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });
$("#nav").addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
$("#logout").addEventListener("click", async () => { await post("/api/logout").catch(() => {}); stopApp(); renderLogin(); });

function skeleton(name) {
  const k = `<div class="grid kpis">${'<div class="skel" style="height:128px"></div>'.repeat(4)}</div>`;
  return name === "overview" ? `${k}<div class="grid two"><div class="skel" style="height:250px"></div><div class="skel" style="height:250px"></div></div>`
    : `<div class="skel" style="height:360px"></div>`;
}

function animate(root) {
  root.classList.remove("enter"); void root.offsetWidth; root.classList.add("enter");
  $$("[data-a]", root).forEach((el, i) => el.style.setProperty("--i", i));
  [$$("tbody tr", root), $$(".feed li", root), $$(".chart rect", root)].forEach((list) =>
    list.forEach((el, i) => el.style.setProperty("--r", Math.min(i, 24))));
  countUp(root);
  clearTimeout(root._t); root._t = setTimeout(() => root.classList.remove("enter"), 2200);
}

async function route() {
  const name = location.hash.slice(1) || "overview";
  const v = VIEWS[name] || VIEWS.overview;
  const my = ++navToken;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${VIEWS[name] ? name : "overview"}`));
  movePill();
  $("#title").textContent = v.title;
  $("#subtitle").textContent = v.sub || "";
  if ($("#appbar-title")) $("#appbar-title").textContent = v.title;
  // раздел со своей шапкой внутри (как ноды) — прячем общий заголовок страницы
  $(".top")?.classList.toggle("bare", !!v.bare);
  $("#actions").innerHTML = "";
  $("#view").innerHTML = skeleton(name);
  clearTimeout(refreshTimer);
  await draw(v, my, true);
  schedule(v, my);
}
window.addEventListener("hashchange", route);

/* ---------- версия и обновление по кнопке (как значок версии в Remnawave) ---------- */
let versionInfo = null, versionTimer = null;

async function loadVersion(force = false) {
  try { versionInfo = await api(`/api/version${force ? "?force=true" : ""}`, { noRedirect: true }); } catch { return; }
  const v = versionInfo, gh = v.github, b = $("#version");
  if (!b) return;
  b.hidden = false;
  b.textContent = `v${v.version}${gh.status === "update" ? " ↑" : ""}`;
  b.className = `version${gh.status === "update" ? " new" : gh.status === "local" || gh.status === "diverged" ? " dev" : ""}`;
  b.title = gh.status === "update" ? `Доступно обновление: ${gh.behind} изм.` : `Коммит ${v.commit.slice(0, 7) || "неизвестен"}`;
  $("#burger")?.classList.toggle("has-update", gh.status === "update");
  clearTimeout(versionTimer); versionTimer = setTimeout(loadVersion, 30 * 60 * 1000);
}

function versionSheet() {
  const v = versionInfo; if (!v) return;
  const gh = v.github, repo = `https://github.com/${v.repo}`;
  const dt = (s) => new Date(s).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const state = { latest: ["ok", "актуальная"], update: ["info", `доступно обновление · ${gh.behind}`], local: ["warn", "локальная, не на GitHub"],
    diverged: ["warn", "разошлась с GitHub"], unknown: ["off", "неизвестно"] }[gh.status] || ["off", gh.status];
  const canUpdate = v.updater.installed && ["update", "diverged", "unknown"].includes(gh.status);
  const sheet = openSheet({
    title: `RemnaDeck <span class="muted">v${esc(v.version)}</span>`, lead: `<img src="/static/logo.svg" alt="" width="20" height="20">`,
    body: `<div class="sec-card"><div class="kv-rows">
        <p><span class="muted">Статус</span> <span class="tag ${state[0]}">${state[1]}</span></p>
        <p><span class="muted">Коммит</span> ${v.commit ? `<a class="mono" href="${repo}/commit/${esc(v.commit)}" target="_blank" rel="noopener">${esc(v.commit.slice(0, 7))}</a>` : `<span class="muted">неизвестен — собрано без install.sh</span>`}</p>
        <p><span class="muted">Собрано</span> ${v.built_at ? dt(v.built_at * 1000) : "—"}</p>
        <p><span class="muted">На GitHub</span> <span>${gh.latest ? `<a class="mono" href="${repo}/commit/${esc(gh.latest.sha)}" target="_blank" rel="noopener">${esc(gh.latest.sha)}</a> · ${dt(gh.latest.date)}` : "—"}</span></p>
        <p><span class="muted">Проверено</span> <span>${ago(gh.checked_at)}${gh.error ? ` · <span style="color:var(--rose)">${esc(gh.error)}</span>` : ""}</span></p>
      </div></div>
      ${gh.commits.length ? `<div class="sec-card"><h3 style="margin:0 0 10px;font-size:15px">Что нового · ${gh.behind}</h3>
        <div class="mini-list">${gh.commits.map((c) => `<div class="mini-row"><span><b>${esc(c.message)}</b>
          <span class="sub mono">${esc(c.sha)} · ${dt(c.date)}</span></span></div>`).join("")}</div></div>` : ""}
      ${gh.status === "local" ? `<p class="hint" style="margin:0">На сервере стоит версия с коммитами, которых ещё нет на GitHub. Отправь их: <code>cd /opt/remnadeck && git push</code></p>` : ""}
      <div class="sec-card" id="upd-box" ${v.updater.state === "running" || v.updater.state === "requested" ? "" : "hidden"}>
        <h3 style="margin:0 0 10px;font-size:15px">Обновление <span class="muted" id="upd-state"></span></h3>
        <pre class="console" id="upd-log" data-scroll style="max-height:40vh;min-height:120px"></pre></div>
      <div class="form-actions">
        ${canUpdate ? `<button class="btn btn-primary" id="upd-go">Обновить</button>` : ""}
        <button class="btn" id="upd-check">Проверить сейчас</button>
        <a class="btn btn-ghost" href="${repo}" target="_blank" rel="noopener">GitHub</a>
      </div>
      ${v.updater.installed ? "" : `<p class="hint" style="margin:0">Обновление по кнопке включается один раз командой на сервере:
        <code>cd /opt/remnadeck && sudo bash install.sh --update</code> — дальше хватит кнопки.</p>`}`,
  });
  $("#upd-check", sheet).addEventListener("click", (e) => busy(e.currentTarget, async () => {
    await loadVersion(true); sheet.close(); versionSheet();
  }));
  $("#upd-go", sheet)?.addEventListener("click", (e) => {
    if (!confirm("Обновить панель? Она перезапустится — на ~20 секунд будет недоступна, подписки в это время Caddy отдаёт напрямую.")) return;
    busy(e.currentTarget, async () => {
      try { await post("/api/version/update"); } catch (x) { return toast(x.message, true); }
      e.currentTarget.hidden = true; followUpdate(sheet, v.commit);
    });
  });
  if (v.updater.state === "running" || v.updater.state === "requested") followUpdate(sheet, v.commit);
}

/* следим за обновлением: лог, пока панель жива; перезапуск — ждём, пока поднимется, и перезагружаемся */
function followUpdate(sheet, fromCommit) {
  const box = $("#upd-box", sheet), logEl = $("#upd-log", sheet), st = $("#upd-state", sheet);
  box.hidden = false;
  const names = { requested: "ждёт сервер…", running: "идёт…", done: "готово", error: "ошибка", idle: "" };
  let down = false;
  const tick = async () => {
    try {
      const u = await api("/api/version/update", { noRedirect: true });
      logEl.textContent = u.log.replace(/\x1b\[[0-9;]*m/g, "") || "…";
      logEl.scrollTop = logEl.scrollHeight;
      st.textContent = names[u.state] ?? u.state;
      if (u.state === "error") return toast("Обновление не удалось — лог в окне", true);
      if (u.state === "done" && (down || u.commit !== fromCommit)) {
        toast("Панель обновлена — перезагружаю");
        return setTimeout(() => location.reload(), 1200);
      }
    } catch { down = true; st.textContent = "панель перезапускается…"; }
    setTimeout(tick, 2000);
  };
  tick();
}

/* ---------- живые данные ----------
   Как в Remnawave (React Query): свой интервал у каждого раздела, следующий запрос — только после
   ответа на предыдущий, во вкладке в фоне опрос стоит, при возврате — сразу свежие данные.
   Фоновое обновление не трогает то, с чем человек работает: пауза, пока курсор в поле ввода,
   в форме несохранённые правки, выделен текст, открыто окно, идёт перетаскивание или раздел
   сам попросил (v.hold — например, выбраны пользователи). */
let lastDraw = 0;

function holdReason(v) {
  const view = $("#view"), a = document.activeElement;
  if (document.hidden) return "вкладка в фоне";
  if (document.body.classList.contains("row-dragging")) return "перетаскивание";
  if (document.body.classList.contains("modal-open")) return "открыто окно";
  if (a && view.contains(a) && a.matches("input:not([type=checkbox]):not([type=radio]),textarea,select")) return "курсор в поле ввода";
  if (view.dataset.dirty) return "в форме несохранённые изменения";
  const sel = getSelection();
  if (sel && !sel.isCollapsed && view.contains(sel.anchorNode)) return "выделен текст";
  return v.hold?.() || null;
}

function schedule(v, token, ms = v.refresh * 1000) {
  clearTimeout(refreshTimer);
  if (!v.refresh || token !== navToken) return;
  refreshTimer = setTimeout(() => tick(v, token), ms);
}

async function tick(v, token) {
  if (token !== navToken) return;
  const why = holdReason(v);
  const btn = $("#ab-refresh");
  if (why) {
    if (btn) btn.title = `Автообновление на паузе: ${why}`;
    return schedule(v, token, 2000); // проверим снова через пару секунд
  }
  if (btn) btn.title = "Обновить раздел";
  await draw(v, token, false);
  schedule(v, token);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden || $("#shell")?.hidden) return;
  loadStatus?.();
  const v = VIEWS[location.hash.slice(1) || "overview"] || VIEWS.overview;
  if (v.refresh && Date.now() - lastDraw > v.refresh * 1000) tick(v, navToken);
});

// правка в форме раздела — автообновление её не затирает, пока не сохранят (сохранение перерисует раздел).
// Поиск и галочки выбора — не правки: у них своя логика
document.addEventListener("input", (e) => {
  const t = e.target, view = $("#view");
  if (!view?.contains(t) || !t.closest("form") || t.matches("[type=search],[data-pick],[data-live]")) return;
  view.dataset.dirty = "1";
}, true);

/* что сохранить при фоновой перерисовке: прокрутку, раскрытые блоки, результаты с [data-keep] */
function snapshot(root) {
  return {
    y: window.scrollY,
    scroll: $$(".table-wrap, .console, [data-scroll]", root).map((el) => [el.scrollLeft, el.scrollTop]),
    open: $$("details", root).map((d) => d.open),
    keep: Object.fromEntries($$("[data-keep][id]", root).map((el) => [el.id, el.innerHTML])),
  };
}
function restore(root, s) {
  $$(".table-wrap, .console, [data-scroll]", root).forEach((el, i) => { if (s.scroll[i]) [el.scrollLeft, el.scrollTop] = s.scroll[i]; });
  $$("details", root).forEach((d, i) => { if (s.open[i] !== undefined) d.open = s.open[i]; });
  Object.entries(s.keep).forEach(([id, html]) => { const el = root.querySelector(`#${CSS.escape(id)}`); if (el && html) el.innerHTML = html; });
  if (Math.abs(window.scrollY - s.y) > 1) window.scrollTo(0, s.y);
}

async function draw(v, token, first) {
  let data;
  const btn = $("#ab-refresh");
  if (!first) btn?.classList.add("syncing");
  try { data = await v.load(); }
  catch (e) {
    btn?.classList.remove("syncing");
    if (token !== navToken || e.message === "Нужен вход") return;
    if (first) $("#view").innerHTML = `<div class="banner">${esc(e.message)}</div>`;
    else if (btn) { btn.classList.add("failed"); btn.title = `Не удалось обновить: ${e.message}`; }
    return;
  }
  btn?.classList.remove("syncing", "failed");
  if (token !== navToken) return;
  const root = $("#view");
  const snap = first ? null : snapshot(root);
  root.innerHTML = v.draw(data);
  delete root.dataset.dirty;
  $("#actions").innerHTML = v.actions ? v.actions(data) : "";
  v.bind?.(data, root);
  if (snap) restore(root, snap);
  lastDraw = Date.now();
  if (first) animate(root);
}
const redraw = (first = true) => draw(VIEWS[location.hash.slice(1) || "overview"] || VIEWS.overview, navToken, first);

/* ---------- экраны ---------- */
function trafficChart(points) {
  const total = points.reduce((s, p) => s + p.bytes, 0);
  if (!total) return `<div class="chart-empty">Данные копятся: график заполнится через час-другой после запуска панели.</div>`;
  const max = Math.max(...points.map((p) => p.bytes));
  const w = 22, gap = 8, h = 140;
  const bars = points.map((p, i) => {
    const bh = Math.max(3, (p.bytes / max) * h);
    return `<rect x="${i * (w + gap)}" y="${h - bh}" width="${w}" height="${bh}" rx="5" fill="url(#g)"><title>${hhmm(p.hour_ts)} — ${fmtBytes(p.bytes)}</title></rect>`;
  }).join("");
  const peak = points.reduce((a, b) => (b.bytes > a.bytes ? b : a));
  return `<div class="chart"><svg viewBox="0 0 ${points.length * (w + gap) - gap} ${h}" preserveAspectRatio="none" role="img" aria-label="Трафик по часам">
    <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#3bc9db"/><stop offset="1" stop-color="#0c8599" stop-opacity=".45"/></linearGradient></defs>${bars}</svg>
    <div class="chart-foot"><span>Всего за сутки: ${fmtBytes(total)}</span><span>Пик в ${hhmm(peak.hour_ts)}: ${fmtBytes(peak.bytes)} за час</span></div></div>`;
}

function feed(events, empty) {
  if (!events.length) return `<p class="empty">${empty || "Пока тихо. Здесь появятся сбои нод, проблемы с путём и твои действия."}</p>`;
  return `<ul class="feed">${events.map((e) => `<li><time title="${new Date(e.ts * 1000).toLocaleString("ru-RU")}">${hhmm(e.ts)}</time><span class="tag ${e.level}">${LEVEL[e.level] || e.level}</span><span>${esc(e.text)}</span></li>`).join("")}</ul>`;
}

const stateTag = (s) => (s === 1 ? `<span class="tag ok">на связи</span>` : s === -1 ? `<span class="tag off">выключена</span>` : `<span class="tag bad">нет связи</span>`);
const statusTag = (s) => `<span class="tag ${s === "ACTIVE" ? "ok" : s === "DISABLED" ? "off" : s === "LIMITED" ? "warn" : "bad"}">${({ ACTIVE: "активен", DISABLED: "отключён", LIMITED: "лимит", EXPIRED: "истёк" })[s] || esc(s)}</span>`;
const kpiCard = (tone, ico, label, value, sub) => `<div class="panel kpi tone-${tone}" data-a>
  <span class="tile">${icon(ico)}</span>
  <div class="kpi-body"><p>${label}</p><div class="num">${value}</div><div class="sub">${sub}</div></div></div>`;

const refreshBtn = `<button class="btn btn-sm btn-ghost" data-refresh>${icon("refresh")}Обновить</button>`;
const bindRefresh = (root) => $("[data-refresh]")?.addEventListener("click", (e) => busy(e.currentTarget, () => redraw(false)));

let usersState = { q: "", status: "", page: 1 };
let eventsLevel = "";
let settingsCache = null;

const VIEWS = {
  overview: {
    title: "Обзор", sub: "Состояние инфраструктуры в реальном времени", refresh: 15,
    load: () => api("/api/overview"),
    actions: () => `<span class="muted" style="font-size:13px">Обновлено в ${hhmm(Date.now() / 1000)}</span>`,
    draw: (d) => {
      const allUp = d.nodes.total && d.nodes.online === d.nodes.total;
      return `
      ${d.errors.map((e) => `<div class="banner" data-a>${esc(e)} <a href="#connection">Открыть подключение</a></div>`).join("")}
      <div class="grid kpis">
        ${kpiCard("green", "server", "Ноды на связи",
          `<span data-count="${d.nodes.online}">${d.nodes.online}</span><small> / ${d.nodes.total}</small>`,
          `<span class="dot ${allUp ? "ok live" : "bad"}"></span>${allUp ? "все отвечают" : d.nodes.total ? `${d.nodes.total - d.nodes.online} без связи` : "нод нет"}`)}
        ${kpiCard("blue", "users", "Активные подписки",
          `<span data-count="${d.users.active ?? 0}">${fmtNum(d.users.active)}</span><small> / ${fmtNum(d.users.total ?? d.users.all ?? 0)}</small>`,
          "всего в Remnawave")}
        ${kpiCard("cyan", "users", "Онлайн сейчас",
          `<span data-count="${d.users.online_now ?? 0}">${fmtNum(d.users.online_now)}</span>`,
          `активных подписок ${fmtNum(d.users.active)}`)}
        ${kpiCard(d.incidents_24h ? "red" : "muted", "bell", "Сбои за сутки",
          `<span data-count="${d.incidents_24h}">${d.incidents_24h}</span>`,
          `<span class="dot ${d.incidents_24h ? "bad" : "ok"}"></span>${d.incidents_24h ? "подробности в событиях" : "без происшествий"}`)}
      </div>
      <div class="grid two">
        <div class="panel" data-a><h2>Трафик за 24 часа <small>все ноды</small></h2>${trafficChart(d.traffic)}</div>
        <div class="panel" data-a><h2>Последние события <a class="btn btn-sm btn-ghost" href="#events">Все события</a></h2>${feed(d.events)}</div>
      </div>
      <div class="panel" data-a><h2>Нагрузка по нодам <small>клиенты онлайн</small></h2>
        ${d.top_nodes.length ? `<div class="bars">${d.top_nodes.map((n) => `<div class="bar-row"><span>${esc(n.name)}</span><div class="bar"><i style="width:${Math.max(n.share, 2)}%"></i></div><span class="muted" style="text-align:right">${n.online}</span></div>`).join("")}</div>` : `<p class="empty">Нет нод на связи.</p>`}
      </div>`;
    },
  },

  users: {
    title: "Пользователи", sub: "Подписки из Remnawave: поиск и быстрые действия",
    load: () => api(`/api/users?${new URLSearchParams(usersState)}`),
    actions: (d) => `<span class="muted" style="font-size:13px">Всего ${fmtNum(d.all)}</span>`,
    draw: (d) => `<div class="panel" data-a>
      <div class="toolbar">
        <div class="search">${icon("search")}<input id="u-q" type="search" placeholder="Имя, Telegram ID или short UUID" value="${esc(usersState.q)}" aria-label="Поиск"></div>
        <div class="seg" id="u-seg">${[["", "Все", d.all], ["ACTIVE", "Активные"], ["DISABLED", "Отключённые"], ["LIMITED", "Лимит"], ["EXPIRED", "Истёкшие"]]
          .map(([k, l, c]) => `<button data-s="${k}" class="${k === usersState.status ? "on" : ""}">${l}<b>${fmtNum(c ?? d.counts[k] ?? 0)}</b></button>`).join("")}</div>
      </div>
      <div id="u-body">${usersBody(d)}</div></div>`,
    bind: (d, root) => {
      let deb;
      $("#u-q", root).addEventListener("input", (e) => {
        clearTimeout(deb);
        deb = setTimeout(() => { usersState = { ...usersState, q: e.target.value, page: 1 }; reloadUsers(); }, 300);
      });
      $$("#u-seg button", root).forEach((b) => b.addEventListener("click", () => {
        $$("#u-seg button").forEach((x) => x.classList.toggle("on", x === b));
        usersState = { ...usersState, status: b.dataset.s, page: 1 }; reloadUsers();
      }));
      bindUsersBody();
    },
  },

  events: {
    title: "События", sub: "Сбои, восстановления и действия администратора", refresh: 30,
    load: () => api(`/api/events?level=${eventsLevel}`),
    actions: () => `<div class="seg" id="ev-seg">${["", "bad", "warn", "ok", "info"].map((l) => `<button data-l="${l}" class="${l === eventsLevel ? "on" : ""}">${l ? LEVEL[l] : "все"}</button>`).join("")}</div>`,
    draw: (rows) => {
      if (!rows.length) return `<div class="panel" data-a>${feed([], "За последние 30 дней событий с таким уровнем нет.")}</div>`;
      const groups = [];
      rows.forEach((e) => { const k = dayLabel(e.ts); if (!groups.length || groups.at(-1).k !== k) groups.push({ k, items: [] }); groups.at(-1).items.push(e); });
      return `<div class="panel" data-a>${groups.map((g) => `<p class="day">${g.k}</p>${feed(g.items)}`).join("")}</div>`;
    },
    bind: () => $$("#ev-seg button").forEach((b) => b.addEventListener("click", () => { eventsLevel = b.dataset.l; redraw(true); })),
  },

  connection: {
    title: "Подключение", sub: "Как панель достаёт данные из Remnawave",
    load: () => api("/api/settings"),
    draw: (s) => `<div class="panel" data-a><form class="form" id="f">
      <div class="form-grid">
        <label>Адрес API Remnawave<input name="remnawave_url" value="${esc(s.remnawave_url)}" required>
          <span class="hint">На этом сервере — http://remnawave:3000, на другом — https://домен-панели</span></label>
        <label>API-токен<input name="remnawave_token" placeholder="${s.remnawave_token ? `сохранён (${esc(s.remnawave_token)}) — оставь пустым` : "не задан"}" autocomplete="off">
          <span class="hint">Remnawave → Настройки → API-токены</span></label>
        <label>Заголовки X-Forwarded-*<select name="remnawave_internal">
          ${[["auto", "Автоматически (для http://)"], ["true", "Всегда добавлять"], ["false", "Не добавлять"]].map(([v, l]) => `<option value="${v}" ${v === s.remnawave_internal ? "selected" : ""}>${l}</option>`).join("")}
        </select><span class="hint">Нужны, когда панель ходит в Remnawave напрямую, мимо прокси</span></label>
        <label>Проверка TLS-сертификата<select name="remnawave_verify_tls">
          <option value="true" ${s.remnawave_verify_tls ? "selected" : ""}>Проверять</option>
          <option value="false" ${!s.remnawave_verify_tls ? "selected" : ""}>Не проверять (самоподписанный)</option>
        </select></label>
        <label>Домен этой панели<input name="panel_domain" value="${esc(s.panel_domain)}">
          <span class="hint">Только запись в .env. Прокси (Caddy/nginx) правится отдельно</span></label>
      </div>
      <div class="check-line" id="check"></div>
      <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button><button class="btn" type="button" id="test">Проверить подключение</button></div>
      ${envNote()}</form></div>`,
    bind: () => {
      const read = () => { const f = Object.fromEntries(new FormData($("#f"))); f.remnawave_verify_tls = f.remnawave_verify_tls === "true"; return f; };
      $("#test").addEventListener("click", (e) => busy(e.currentTarget, async () => {
        try { const r = await post("/api/settings/test-remnawave", read()); checkLine(true, `Работает: ${r.nodes} нод, ${r.latency_ms} мс`); }
        catch (x) { checkLine(false, x.message); }
      }));
      $("#f").addEventListener("submit", (e) => { e.preventDefault(); const f = read(); if (!f.remnawave_token) delete f.remnawave_token; save(e.submitter, f); });
    },
  },

  notifications: {
    title: "Уведомления", sub: "Алерты в Telegram о сбоях и восстановлениях",
    load: () => api("/api/settings"),
    draw: (s) => `<div class="panel" data-a><form class="form" id="f">
      <p style="margin:0" class="muted">Бот пишет, когда нода теряет связь или возвращается, когда ломается путь до хоста и когда сертификату осталось меньше 7 дней.</p>
      <div class="form-grid">
        <label>Токен бота<input name="tg_bot_token" placeholder="${s.tg_bot_token ? `сохранён (${esc(s.tg_bot_token)}) — оставь пустым` : "123456:ABC…"}" autocomplete="off"></label>
        <label>ID чата<input name="tg_chat_id" value="${esc(s.tg_chat_id)}" placeholder="например 123456789">
          <span class="hint">Бота нужно запустить в этом чате (или добавить в группу)</span></label>
      </div>
      <div class="check-line" id="check"></div>
      <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button><button class="btn" type="button" id="test">Отправить тест</button>
        ${s.tg_bot_token ? `<button class="btn btn-ghost btn-danger" type="button" id="off">Отключить уведомления</button>` : ""}</div>
      ${envNote()}</form></div>`,
    bind: () => {
      const read = () => Object.fromEntries(new FormData($("#f")));
      $("#test").addEventListener("click", (e) => busy(e.currentTarget, async () => {
        try { await post("/api/settings/test-telegram", read()); checkLine(true, "Отправлено — проверь Telegram"); } catch (x) { checkLine(false, x.message); }
      }));
      $("#off")?.addEventListener("click", (e) => { if (confirm("Отключить уведомления?")) save(e.currentTarget, { tg_bot_token: "", tg_chat_id: "" }, true); });
      $("#f").addEventListener("submit", (e) => { e.preventDefault(); const f = read(); if (!f.tg_bot_token) delete f.tg_bot_token; save(e.submitter, f, true); });
    },
  },

  monitoring: {
    title: "Опрос и проверки", sub: "Как часто панель обращается к Remnawave и проверяет хосты",
    load: () => api("/api/settings"),
    draw: (s) => `<div class="panel" data-a><form class="form" id="f">
      <div class="form-grid">
        <label>Опрос нод, секунд<input name="poll_interval" type="number" min="15" max="3600" value="${s.poll_interval}">
          <span class="hint">Статусы нод, трафик, события. 60 — разумно</span></label>
        <label>Проверка хостов, секунд<input name="check_interval" type="number" min="60" max="86400" value="${s.check_interval}">
          <span class="hint">DNS, порт и TLS каждого хоста. 300 — разумно</span></label>
      </div>
      <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div>
      ${envNote()}</form></div>`,
    bind: () => $("#f").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      save(e.submitter, { poll_interval: Number(f.poll_interval), check_interval: Number(f.check_interval) });
    }),
  },

  statuspage: {
    title: "Статус-страница", sub: "Публичная страница с состоянием нод для клиентов",
    load: () => api("/api/status-page"),
    actions: (s) => `<a class="btn btn-sm btn-ghost" href="/status" target="_blank" rel="noopener">${icon("globe")}Превью</a>
      ${s.status_domain && s.enabled ? `<a class="btn btn-sm" href="https://${esc(s.status_domain)}" target="_blank" rel="noopener">${esc(s.status_domain)}</a>` : ""}`,
    draw: (s) => `<div class="panel" data-a><form class="form" id="f">
      <label class="check"><input type="checkbox" name="enabled" ${s.enabled ? "checked" : ""}><span>Страница включена — клиенты её видят</span></label>
      <div class="form-grid">
        <label>Заголовок<input name="title" value="${esc(s.title)}" required maxlength="60"></label>
        <label>Домен страницы<input name="status_domain" value="${esc(s.status_domain)}" placeholder="status.example.com">
          <span class="hint">На нём открывается только статус-страница, админка там недоступна</span></label>
      </div>
      <label>Объявление<textarea name="announce" rows="2" maxlength="500" placeholder="Например: плановые работы 28.09 с 03:00 до 04:00 МСК">${esc(s.announce)}</textarea>
        <span class="hint">Плашка над списком. Пустое — не показывается</span></label>
      <div class="checks">
        ${[["info", "Информация"], ["warn", "Предупреждение"], ["bad", "Сбой"]].map(([v, l]) => `<label class="check"><input type="radio" name="announce_level" value="${v}" ${s.announce_level === v ? "checked" : ""}><span>${l}</span></label>`).join("")}
      </div>
      <div class="checks">
        <label class="check"><input type="checkbox" name="show_load" ${s.show_load ? "checked" : ""}><span>Нагрузка нод</span></label>
        <label class="check"><input type="checkbox" name="show_incidents" ${s.show_incidents ? "checked" : ""}><span>Сбои за неделю</span></label>
      </div>
      <label>Ноды на странице<span class="hint">Адреса, трафик и точный онлайн наружу не уходят. Пустое публичное имя — как в Remnawave</span></label>
      ${s.error ? `<div class="banner">${esc(s.error)}</div>` : `<div class="table-wrap"><table><thead><tr><th></th><th>Нода</th><th>Публичное имя</th></tr></thead>
        <tbody>${s.nodes.map((n) => `<tr data-uuid="${esc(n.uuid)}">
          <td style="width:36px"><input type="checkbox" data-show ${n.show ? "checked" : ""} aria-label="Показывать ${esc(n.name)}" style="width:16px;height:16px;accent-color:var(--mint)"></td>
          <td>${flagOf(n.country)} ${esc(n.name)} <span class="sub">${stateTag(n.state)}</span></td>
          <td><input data-name value="${esc(n.public_name)}" maxlength="40" placeholder="${esc(n.name)}"></td></tr>`).join("")}</tbody></table></div>`}
      <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div>
      <p class="hint" style="margin:0">Для своего домена: A-запись на этот сервер и блок в Caddyfile Remnawave —
        <code>status.example.com { reverse_proxy remnadeck:8090 }</code>. Превью работает и в выключенном виде, но только для тебя.</p>
    </form></div>`,
    bind: (s) => $("#f").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target, fd = new FormData(f);
      const nodes = Object.fromEntries($$("tr[data-uuid]", f).map((tr) => [tr.dataset.uuid,
        { show: $("[data-show]", tr).checked, name: $("[data-name]", tr).value.trim() }]));
      busy(e.submitter, async () => {
        try {
          await api("/api/status-page", { method: "PUT", body: JSON.stringify({
            enabled: f.enabled.checked, title: fd.get("title"), status_domain: fd.get("status_domain"),
            announce: fd.get("announce"), announce_level: fd.get("announce_level") || "info",
            show_load: f.show_load.checked, show_incidents: f.show_incidents.checked,
            nodes: s.error ? undefined : nodes }) });
          toast("Сохранено"); redraw(false);
        } catch (x) { toast(x.message, true); }
      });
    }),
  },

  subproxy: {
    title: "Прослойка подписок", sub: "RemnaDeck между клиентом и subscription-page: подставляет свои значения в тексты Remnawave, ничего туда не записывая", refresh: 15,
    load: async () => {
      const [s, texts] = await Promise.all([api("/api/sub-proxy"), api("/api/sub-proxy/texts").catch((e) => ({ error: e.message }))]);
      return { ...s, texts };
    },
    draw: (s) => {
      const st = s.stats, wired = st.last_request > 0, t = s.texts;
      const dom = s.sub_domain || "sub.example.com";
      const hl = (x) => esc(x).replace(/\{\{(DESCRIPTION|RD_[A-Z_]+(?::[^}]*)?)\}\}/g, (m, k) => `<b class="${k === "DESCRIPTION" ? "t-old" : "t-rd"}">{{${k}}}</b>`);
      return `
      <div class="panel" data-a><h2>Состояние</h2>
        ${!s.sub_domain ? `<p class="muted">Домен подписок не задан — прослойка не работает.</p>`
          : !wired ? `<div class="banner">Запросы подписок через панель ещё не шли. Поправь блок ${esc(dom)} в Caddyfile — пример внизу.</div>`
          : `<div>
            <p>${Date.now() / 1000 - st.last_request < 600 ? `<span class="tag ok">идёт трафик</span>` : `<span class="tag warn">тишина</span>`} последний запрос ${ago(st.last_request)} · всего ${fmtNum(st.requests)} с ${hhmm(st.since)}</p>
            <p class="muted">подписок ${fmtNum(st.subs)} · изменено ${fmtNum(st.changed)} · <a href="#bans">забанено ${fmtNum(st.banned || 0)}</a> · ошибок разбора ${fmtNum(st.errors)} · обрывов от subscription-page ${fmtNum(st.upstream_errors)} <span title="Браузеры subscription-page рвёт сам по правилам SRR — это норма">ⓘ</span>
              ${st.p50_ms !== null ? ` · задержка ${st.p50_ms} мс (p95 ${st.p95_ms} мс)` : ""}</p>
            ${st.last_error ? `<p class="muted">последняя ошибка: <code>${esc(st.last_error)}</code></p>` : ""}</div>`}
      </div>
      <div class="panel" data-a><h2>Тексты в Remnawave</h2>
        <p class="muted" style="margin-top:0">Тексты пишутся один раз — в Remnawave (объявление, названия и описания хостов). RemnaDeck только подставляет значения:
          <code>{{RD_QUOTA}}</code> — счётчик по шаблону правила, <code>{{RD_QUOTA_LEFT}}</code> <code>{{RD_QUOTA_USED}}</code> <code>{{RD_QUOTA_LIMIT}}</code>
          <code>{{RD_QUOTA_PERCENT}}</code> <code>{{RD_QUOTA_RESET}}</code> — числа, <code>{{RD_MESSAGE}}</code> — сообщения автоматизаций.
          Конкретное правило: <code>{{RD_QUOTA_LEFT:Обход}}</code>. Строка плашки, где все подстановки пустые, у клиента убирается.</p>
        ${t.error ? `<div class="banner">${esc(t.error)}</div>` : `
          ${t.headers.map((h) => `<p class="muted" style="margin:10px 0 4px">Заголовок <code>${esc(h.name)}</code></p><pre class="console" style="max-height:none;min-height:0">${hl(h.text)}</pre>`).join("")}
          ${t.hosts.length ? `<p class="muted" style="margin:10px 0 4px">Хосты</p>${t.hosts.map((h) => `<p style="margin:2px 0">${hl(h.remark)}${h.description ? ` <span class="muted">· ${hl(h.description)}</span>` : ""}</p>`).join("")}` : ""}
          ${!t.headers.length && !t.hosts.length ? `<p class="muted">Подстановок пока нигде нет.</p>` : ""}
          <div class="form-actions" style="margin-top:14px">
            ${t.legacy ? `<button class="btn btn-primary btn-wrap" id="migrate">Заменить {{DESCRIPTION}} на {{RD_QUOTA}}</button>` : ""}
            ${t.counter_users ? `<button class="btn btn-wrap" id="clean">Очистить описания от счётчиков (${fmtNum(t.counter_users)})</button>` : ""}
          </div>
          ${t.legacy ? `<p class="hint">Заменит <code>{{DESCRIPTION}}</code> в заголовках подписки и хостах. Делай, когда подписки уже идут через прослойку с включённой правкой.</p>` : ""}
          ${t.counter_users ? `<p class="hint">Описания, где лежит только счётчик квоты, станут пустыми, а запись счётчика в описание выключится во всех правилах. Ручные заметки не трогаются.</p>` : ""}`}
      </div>
      <div class="panel" data-a><form class="form" id="f">
        <label class="check"><input type="checkbox" name="enabled" ${s.enabled ? "checked" : ""}><span>Править подписки на лету (выключено — чистый прокси, всё как есть)</span></label>
        <div class="form-grid">
          <label>Домен подписок<input name="sub_domain" value="${esc(s.sub_domain)}" placeholder="sub.example.com">
            <span class="hint">На нём панель только проксирует подписки, админка недоступна</span></label>
          <label>subscription-page<input name="sub_upstream" value="${esc(s.sub_upstream)}" required></label>
        </div>
        <div class="checks">
          <label class="check"><input type="checkbox" name="hide_info_without_quota" ${s.hide_info_without_quota ? "checked" : ""}><span>Скрывать хост, где все подстановки пустые (не под квотой)</span></label>
        </div>
        <label>Состояние нод<span class="hint">Ноды хоста — из привязки в Remnawave, а если её нет — нода с тем же адресом или IP</span></label>
        <div class="checks">
          <label class="check"><input type="checkbox" name="mark_down" ${s.mark_down ? "checked" : ""}><span>Помечать хост без связи</span></label>
          <label class="check"><input type="checkbox" name="down_to_end" ${s.down_to_end ? "checked" : ""}><span>…и уносить в конец списка</span></label>
          <label class="check"><input type="checkbox" name="hide_disabled" ${s.hide_disabled ? "checked" : ""}><span>Скрывать хосты выключенных нод</span></label>
        </div>
        <label>Пометка<input name="down_prefix" value="${esc(s.down_prefix)}" maxlength="16" style="max-width:200px"></label>
        <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div>
        <p class="hint" style="margin:0">Ссылки (base64) и Xray JSON — Happ, v2rayNG, v2rayN, Streisand — разбираются целиком. В Mihomo/Clash и sing-box
          подстановки меняются в тексте, но скрыть хост там нельзя. Скрыть хост ≠ закрыть доступ: доступ режут сквады.</p>
      </form></div>
      <div class="panel" data-a><h2>Проверить на пользователе</h2>
        <form class="toolbar" id="pv"><div class="search">${icon("search")}<input name="username" placeholder="Имя пользователя" required></div>
          <button class="btn" type="submit">Показать</button></form>
        <div id="pv-out" data-keep></div>
      </div>
      <div class="panel" data-a><h2>Caddyfile</h2>
        <p class="muted" style="margin-top:0">Замени блок домена подписок. Если RemnaDeck не отвечает, Caddy сам пойдёт напрямую в subscription-page — подписки не пропадут, просто будут без правок.</p>
        <pre class="console" style="max-height:none">https://${esc(dom)} {
    encode
    reverse_proxy remnadeck:8090 ${esc(s.sub_upstream.replace(/^https?:\/\//, ""))} {
        lb_policy first
        lb_try_duration 5s
        fail_duration 30s
    }
}</pre>
      </div>`;
    },
    bind: (s) => {
      $("#f").addEventListener("submit", (e) => {
        e.preventDefault();
        const f = e.target, fd = new FormData(f);
        const chk = (n) => f[n].checked;
        busy(e.submitter, async () => {
          try {
            await api("/api/sub-proxy", { method: "PUT", body: JSON.stringify({
              enabled: chk("enabled"), hide_info_without_quota: chk("hide_info_without_quota"),
              mark_down: chk("mark_down"), down_to_end: chk("down_to_end"), hide_disabled: chk("hide_disabled"),
              down_prefix: fd.get("down_prefix"), sub_domain: fd.get("sub_domain"), sub_upstream: fd.get("sub_upstream") }) });
            toast("Сохранено"); redraw(false);
          } catch (x) { toast(x.message, true); }
        });
      });
      $("#migrate")?.addEventListener("click", (e) => {
        if (!confirm("Заменить {{DESCRIPTION}} на {{RD_QUOTA}} в настройках подписки и хостах Remnawave?")) return;
        busy(e.currentTarget, async () => {
          try { const r = await post("/api/sub-proxy/migrate"); toast(`Готово: ${r.changed.join(", ") || "нечего менять"}`); redraw(false); }
          catch (x) { toast(x.message, true); }
        });
      });
      $("#clean")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
        try {
          const d = await post("/api/sub-proxy/clean-descriptions", { dry: true });
          if (!confirm(`Очистить описание у ${d.count} пользователей? Например: ${d.sample.join(" · ")}\nЗапись счётчика в описание выключится во всех правилах квот.`)) return;
          const r = await post("/api/sub-proxy/clean-descriptions", { dry: false });
          toast(`Очищено: ${r.cleaned}${r.errors.length ? `, ошибок ${r.errors.length}` : ""}`, !!r.errors.length); redraw(false);
        } catch (x) { toast(x.message, true); }
      }));
      $("#pv").addEventListener("submit", (e) => {
        e.preventDefault();
        busy(e.submitter, async () => {
          const out = $("#pv-out");
          try {
            const r = await post("/api/sub-proxy/preview", { username: new FormData(e.target).get("username") });
            const stTag = (x) => ({ up: `<span class="tag ok">на связи</span>`, down: `<span class="tag bad">нет связи</span>`,
              disabled: `<span class="tag off">выключена</span>` })[x] || `<span class="tag off">не привязан</span>`;
            out.innerHTML = `
              ${r.enabled ? "" : `<div class="banner">Правка выключена — сейчас клиенты получают подписку без изменений. Ниже — как будет.</div>`}
              <p><b>${esc(r.username)}</b> <span class="muted">· ${esc(r.short_uuid)}</span></p>
              <p class="muted">Квоты: ${r.quotas.length ? r.quotas.map((q) => `${esc(q.name)} — <b>${esc(q.text)}</b> (осталось ${esc(q.left)} из ${esc(q.limit)} ГБ)`).join(" · ") : "не под квотой"}</p>
              ${r.messages.length ? `<p class="muted">Сообщения автоматизаций: ${r.messages.map(esc).join(" / ")}</p>` : ""}
              ${r.announce !== null ? `<p class="muted" style="margin-bottom:4px">Плашка Happ (подстановки Remnawave вроде {{USERNAME}} клиент увидит уже заполненными)</p>
                <pre class="console" style="max-height:none;min-height:0">${esc(r.announce || "— пусто —")}</pre>` : ""}
              <div class="table-wrap"><table><thead><tr><th>Хост в Remnawave</th><th>Ноды</th><th>Клиент увидит</th></tr></thead><tbody>
              ${r.hosts.map((h) => `<tr><td>${esc(h.remark)}</td><td>${stTag(h.state)}</td>
                <td>${h.result === null ? `<span class="muted">скрыт</span>` : h.result === h.remark && !h.to_end ? `<span class="muted">без изменений</span>`
                  : `<b>${esc(h.result)}</b>${h.to_end ? ` <span class="muted">· в конце</span>` : ""}`}</td></tr>`).join("")}
              </tbody></table></div>
              <p class="hint">Хосты, которые скрыты сквадами или форматом, Remnawave в подписку не отдаст — здесь показаны все включённые.</p>`;
          } catch (x) { out.innerHTML = `<div class="banner">${esc(x.message)}</div>`; }
        });
      });
    },
  },

  account: {
    title: "Учётная запись", sub: "Логин и пароль администратора",
    load: () => api("/api/settings"),
    draw: (s) => `<div class="panel" data-a><form class="form" id="f">
      <label>Логин<input name="username" value="${esc(s.username)}" required minlength="3" maxlength="32" autocomplete="username"></label>
      <div class="form-grid">
        <label>Новый пароль<input name="new_password" type="password" minlength="8" autocomplete="new-password" placeholder="оставь пустым, чтобы не менять"></label>
        <label>Новый пароль ещё раз<input name="new_password2" type="password" autocomplete="new-password"></label>
      </div>
      <label>Текущий пароль<input name="current_password" type="password" required autocomplete="current-password">
        <span class="hint">После сохранения все остальные сессии завершатся</span></label>
      <p class="form-error" id="err"></p>
      <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div>
      ${envNote()}</form></div>`,
    bind: () => $("#f").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      $("#err").textContent = "";
      if (f.new_password && f.new_password.length < 8) return ($("#err").textContent = "Новый пароль — минимум 8 символов");
      if (f.new_password !== f.new_password2) return ($("#err").textContent = "Новые пароли не совпадают");
      busy(e.submitter, async () => {
        try {
          await post("/api/settings/account", { username: f.username, current_password: f.current_password, new_password: f.new_password });
          toast("Сохранено. Остальные сессии завершены"); loadStatus(); redraw(false);
        } catch (x) { $("#err").textContent = x.message; }
      });
    }),
  },
};

function envNote() { return `<div class="env-note">${icon("key")}<span>Сохраняется в файл <code>.env</code> рядом с docker-compose.yml</span></div>`; }
function checkLine(ok, text) { $("#check").innerHTML = `<span class="dot ${ok ? "ok" : "bad"}"></span>${esc(text)}`; }
async function save(btn, body, silentStatus) {
  await busy(btn, async () => {
    try { await api("/api/settings", { method: "PUT", body: JSON.stringify(body) }); toast("Сохранено в .env"); if (!silentStatus) loadStatus(); redraw(false); }
    catch (x) { toast(x.message, true); }
  });
}

function usersBody(d) {
  if (!d.items.length) return `<p class="empty">Никого не нашлось. Измени запрос или фильтр.</p>`;
  return `<div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Статус</th><th class="num">Трафик</th><th>Истекает</th><th>Был онлайн</th><th></th></tr></thead>
    <tbody>${d.items.map((u) => `<tr>
      <td><b>${esc(u.username)}</b>${u.telegram_id ? `<span class="sub">tg ${esc(u.telegram_id)}</span>` : ""}</td>
      <td>${statusTag(u.status)}</td>
      <td class="num">${fmtBytes(u.used)}<span class="sub">${u.limit ? "из " + fmtBytes(u.limit) : "без лимита"}</span></td>
      <td>${u.expire_at ? new Date(u.expire_at).toLocaleDateString("ru-RU") : "—"}</td>
      <td class="muted">${ago(u.online_at)}</td>
      <td><div class="actions">
        ${u.status === "DISABLED" ? `<button class="btn btn-sm" data-user="${u.uuid}" data-act="enable">Включить</button>` : `<button class="btn btn-sm btn-danger" data-user="${u.uuid}" data-act="disable">Отключить</button>`}
        <button class="btn btn-sm" data-user="${u.uuid}" data-act="reset-traffic">Сбросить трафик</button>
      </div></td></tr>`).join("")}</tbody></table></div>
    <div class="pager">Найдено ${fmtNum(d.total)} · страница ${d.page} из ${d.pages}
      <button class="btn btn-sm" id="u-prev" ${d.page <= 1 ? "disabled" : ""}>Назад</button>
      <button class="btn btn-sm" id="u-next" ${d.page >= d.pages ? "disabled" : ""}>Дальше</button></div>`;
}

async function reloadUsers() {
  const body = $("#u-body"); if (!body) return;
  body.style.opacity = ".5";
  try {
    const d = await api(`/api/users?${new URLSearchParams(usersState)}`);
    body.innerHTML = usersBody(d);
    bindUsersBody();
  } catch (e) { toast(e.message, true); }
  body.style.opacity = "";
}

function bindUsersBody() {
  $("#u-prev")?.addEventListener("click", () => { usersState.page--; reloadUsers(); });
  $("#u-next")?.addEventListener("click", () => { usersState.page++; reloadUsers(); });
  $$("[data-user]").forEach((b) => b.addEventListener("click", () => {
    const names = { enable: "Включить пользователя", disable: "Отключить пользователя", "reset-traffic": "Сбросить трафик" };
    if (b.dataset.act !== "enable" && !confirm(`${names[b.dataset.act]}?`)) return;
    busy(b, async () => {
      try { await post(`/api/users/${b.dataset.user}/${b.dataset.act}`); toast("Готово"); reloadUsers(); } catch (e) { toast(e.message, true); }
    });
  }));
}

/* ---------- старт ---------- */
(async () => {
  try {
    const s = await api("/api/state", { noRedirect: true });
    if (s.setup) { wiz.data.remnawave_url = s.remnawave_url || wiz.data.remnawave_url; renderSetup(); }
    else if (s.user) startApp();
    else renderLogin();
  } catch (e) {
    authCard(`<h2>Панель недоступна</h2><p>${esc(e.message)}</p>`);
  }
})();

/* PWA: ставится на телефон как приложение */
if ("serviceWorker" in navigator && location.protocol === "https:") {
  /* Установленное приложение неделями живёт в фоне и не перезагружается само. Поэтому при каждом
     возврате в него просим браузер проверить sw.js; вышла новая версия — новый воркер забирает
     страницу, и мы перезагружаемся. Если открыта форма — ждём, пока её закроют или свернут приложение. */
  const hadController = !!navigator.serviceWorker.controller;
  let pending = false;
  const editing = () => !!document.querySelector(".modal") || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  const reloadSoon = () => {
    if (document.hidden || !editing()) return location.reload();
    if (!pending) { pending = true; toast("Вышла новая версия панели — обновится после закрытия окна"); }
  };
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController) reloadSoon(); });
  document.addEventListener("visibilitychange", () => { if (pending && document.hidden) location.reload(); });
  setInterval(() => { if (pending && !editing()) location.reload(); }, 3000);

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).then((reg) => {
      const check = () => reg.update().catch(() => {});
      document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
      setInterval(check, 30 * 60 * 1000);
    }).catch(() => {});
  });
}
