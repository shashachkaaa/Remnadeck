/* RemnaDeck — управление: ноды, хосты, серверы, CDN, задачи.
   Грузится после app.js и переопределяет раздел «Ноды». */

/* ---------- модальные окна ---------- */
let openWindows = 0;

function lockScroll(on) {
  openWindows = Math.max(0, openWindows + (on ? 1 : -1));
  document.body.classList.toggle("modal-open", openWindows > 0);
}

function closeModal(el) {
  if (el.dataset.locked) { delete el.dataset.locked; lockScroll(false); }
  el.classList.remove("show");
  setTimeout(() => el.remove(), 180);
}

function openModal({ title, body, submit = "Сохранить", wide = false, danger = false, onSubmit }) {
  const el = document.createElement("div");
  el.className = "modal-wrap";
  el.innerHTML = `<div class="modal${wide ? " wide" : ""}">
    <header><h3>${esc(title)}</h3><button type="button" class="icon-btn" data-close aria-label="Закрыть">
      <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>
    <form id="m-form" novalidate><div class="modal-body">${body}</div>
      <p class="form-error" id="m-err"></p>
      <div class="modal-foot"><button type="button" class="btn btn-ghost" data-close>Отмена</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" type="submit">${esc(submit)}</button></div>
    </form></div>`;
  document.body.appendChild(el);
  el.dataset.locked = "1";
  lockScroll(true);
  requestAnimationFrame(() => el.classList.add("show"));
  const close = () => closeModal(el);
  $$("[data-close]", el).forEach((b) => b.addEventListener("click", close));
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  document.addEventListener("keydown", function esckey(e) {
    if (e.key === "Escape" && document.body.contains(el)) { close(); document.removeEventListener("keydown", esckey); }
  });
  const form = $("#m-form", el);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    $("#m-err", el).textContent = "";
    const values = Object.fromEntries(new FormData(form));
    busy(submitBtn(form, e), async () => {
      try {
        const keep = await onSubmit(values, form, el);
        if (keep !== "keep") close();
      } catch (x) { $("#m-err", el).textContent = x.message; }
    });
  });
  setTimeout(() => $("input,select,textarea", el)?.focus(), 60);
  return el;
}

/* окно без формы: карточка ноды и прочие панели действий */
function openSheet({ title, tag, subtitle, body, wide = true, lead = "", uuid = "" }) {
  const el = document.createElement("div");
  el.className = "modal-wrap";
  el.innerHTML = `<div class="modal${wide ? " wide" : ""}">
    <header class="sheet-head">
      ${lead ? `<span class="row-tile">${lead}</span>` : ""}
      <span class="sheet-title"><h3>${title}</h3>
        ${uuid ? `<span class="sheet-id">${esc(uuid)}</span>` : ""}</span>
      ${tag || ""}
      <button type="button" class="icon-btn" data-close aria-label="Закрыть">
        <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>
    ${subtitle ? `<p class="sheet-sub">${subtitle}</p>` : ""}
    <div class="modal-body" id="sheet-body">${body}</div></div>`;
  document.body.appendChild(el);
  el.dataset.locked = "1";
  lockScroll(true);
  requestAnimationFrame(() => el.classList.add("show"));
  const close = () => closeModal(el);
  $$("[data-close]", el).forEach((b) => b.addEventListener("click", close));
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  document.addEventListener("keydown", function esckey(e) {
    if (e.key === "Escape" && document.body.contains(el)) { close(); document.removeEventListener("keydown", esckey); }
  });
  el.close = close;
  return el;
}

function confirmModal(title, text, onYes, word = "Удалить") {
  openModal({ title, submit: word, danger: true, body: `<p class="muted">${esc(text)}</p>`, onSubmit: onYes });
}

/* ---------- консоль фоновых задач ---------- */
function jobConsole(jobId, title) {
  const el = document.createElement("div");
  el.className = "modal-wrap";
  el.innerHTML = `<div class="modal wide">
    <header><h3>${esc(title)}</h3>
      <span class="tag info" id="j-state">выполняется</span>
      <button type="button" class="icon-btn" data-close aria-label="Закрыть">
        <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>
    <pre class="console" id="j-log">Подключаюсь к серверу…</pre>
    <div class="modal-foot"><span class="muted" id="j-hint">Окно можно закрыть — задача продолжится</span>
      <button type="button" class="btn btn-ghost" data-close>Закрыть</button></div></div>`;
  document.body.appendChild(el);
  el.dataset.locked = "1";
  lockScroll(true);
  requestAnimationFrame(() => el.classList.add("show"));
  const log = $("#j-log", el);
  let since = 0, timer = null, first = true;
  const stop = () => clearInterval(timer);
  $$("[data-close]", el).forEach((b) => b.addEventListener("click", () => { stop(); closeModal(el); }));

  const tick = async () => {
    let d;
    try { d = await api(`/api/jobs/${jobId}?since=${since}`); }
    catch (e) { stop(); log.textContent += `\n${e.message}`; return; }
    if (d.lines.length) {
      if (first) { log.textContent = ""; first = false; }
      log.textContent += d.lines.join("\n") + "\n";
      log.scrollTop = log.scrollHeight;
      since = d.total;
    }
    if (d.status !== "running") {
      stop();
      const ok = d.status === "ok";
      $("#j-state", el).className = `tag ${ok ? "ok" : "bad"}`;
      $("#j-state", el).textContent = ok ? "готово" : "ошибка";
      $("#j-hint", el).textContent = ok ? "Задача завершена" : "Задача завершилась с ошибкой";
      if (ok) toast("Задача выполнена"); else toast("Задача завершилась с ошибкой", true);
      loadStatus();
      redraw(false);
    }
  };
  tick();
  timer = setInterval(tick, 1200);
  loadStatus();                       // значок задачи в шапке — сразу
  return el;
}

const runJob = async (btn, path, body, title) => busy(btn, async () => {
  try { const r = await post(path, body); jobConsole(r.job, title); }
  catch (e) { toast(e.message, true); }
});

const nodesView = { q: "", compact: false, searchOpen: false };

/* скорость: байты/с -> как в Remnawave, биты: Kb/s или Mb/s */
function speed(bps) {
  if (bps == null) return "—";
  const bits = bps * 8;
  if (bits >= 1e9) return `${(bits / 1e9).toFixed(2)} Gb/s`;
  if (bits >= 1e6) return `${(bits / 1e6).toFixed(2)} Mb/s`;
  return `${(bits / 1e3).toFixed(2)} Kb/s`;
}

/* для плиток: число крупно, единица мелко — чтобы влезало в одну строку на телефоне */
const speedKpi = (bps) => { const [v, u] = speed(bps).split(" "); return u ? `${v}<small> ${u}</small>` : v; };

/* аптайм: секунды -> 2d / 5h / 12m */
function uptime(sec) {
  if (!sec) return "—";
  if (sec >= 86400) return `${Math.floor(sec / 86400)}d`;
  if (sec >= 3600) return `${Math.floor(sec / 3600)}h`;
  return `${Math.max(1, Math.floor(sec / 60))}m`;
}

function nodeCard(n, srv) {
  const tone = n.state === 1 ? "up" : n.state === -1 ? "off" : "down";
  const pct = n.traffic_limit ? Math.min(100, n.traffic / n.traffic_limit * 100) : null;
  const live = n.state === 1;
  return `<article class="card-row ${tone}" data-open="${n.uuid}" tabindex="0" role="button"
    aria-label="Открыть ноду ${esc(n.name)}">
    <header>
      <span class="row-tile">${icon(live ? "pulse" : n.state === -1 ? "power" : "server")}</span>
      <span class="chip">${icon("users")}<b>${n.online}</b></span>
      <span class="flag" aria-hidden="true">${flagOf(n.country)}</span>
      <b class="name">${esc(n.name)}</b>
      <span class="grip" data-drag title="Перетащи, чтобы изменить порядок">
        <svg viewBox="0 0 24 24"><path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01" stroke-width="2.6"/></svg></span>
    </header>
    <div class="meter"><b class="big">${fmtBytes(n.traffic)}</b>
      <span class="lim">${pct === null ? "∞" : `${Math.round(pct)}% из ${fmtBytes(n.traffic_limit)}`}</span></div>
    ${pct === null ? "" : `<div class="bar"><i style="width:${Math.max(pct, 2)}%"></i></div>`}
    <div class="row-meta">
      ${srv ? `<span class="pill accent">SSH</span>` : ""}
      ${n.xray ? `<span class="pill">Xray ${esc(n.xray)}</span>` : ""}
      <span class="uptime ${live ? "" : "off"}">${icon("clock")}${live ? uptime(n.uptime) : n.state === -1 ? "выключена" : "offline"}</span>
    </div>
    <footer class="live">
      <span class="ram" title="Память">${icon("ram")}
        <span class="mini-bar"><i style="width:${live && n.mem_pct != null ? n.mem_pct : 0}%"></i></span>
        <b>${live && n.mem_pct != null ? n.mem_pct + "%" : "—"}</b></span>
      <span class="spd up">${icon("up")}<b>${live ? speed(n.tx_bps) : "—"}</b></span>
      <span class="spd down">${icon("down")}<b>${live ? speed(n.rx_bps) : "—"}</b></span>
    </footer>
    ${n.state === 0 && n.message ? `<p class="row-err">${esc(String(n.message).slice(0, 120))}</p>` : ""}
  </article>`;
}

/* флаг страны из кода: RU -> 🇷🇺 */
const flagOf = (cc) => (/^[A-Za-z]{2}$/.test(cc || "") && cc.toUpperCase() !== "XX"
  ? String.fromCodePoint(...[...cc.toUpperCase()].map((c) => 0x1f1a5 + c.charCodeAt(0)))
  : "🏳");

function bindCards(root, open) {
  $$("[data-open]", root).forEach((el) => {
    el.addEventListener("click", (e) => { if (!e.target.closest("[data-drag],[data-pick]")) open(el.dataset.open); });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(el.dataset.open); }
    });
  });
}

/* ---------- перетаскивание строк ---------- */
const GRIP = `<td class="grip" data-drag title="Перетащи, чтобы изменить порядок">
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01"
    stroke-width="2.6"/></svg></td>`;

/* Своя реализация на pointer-событиях: HTML5 drag&drop не работает на телефоне.

   Важно: перетаскиваемую карточку в DOM не двигаем никогда — только соседей.
   Перемещение элемента в DOM снимает с него захват указателя (pointer capture),
   и браузер перестаёт слать события, пока палец случайно не окажется над ручкой.
   Именно это давало «залипание» при движении вверх. Слушаем window, а не ручку. */
const EASE = "cubic-bezier(.2,.8,.2,1)";

/* FLIP: запоминаем, где сосед был, переставляем, и плавно доводим его с
   прежнего места на новое — вместо прыжка */
function flip(el) {
  const before = el.getBoundingClientRect().top;
  return () => {
    const dy = before - el.getBoundingClientRect().top;
    if (!dy) return;
    el.style.transition = "none";
    el.style.transform = `translateY(${dy}px)`;
    void el.offsetHeight;                       // фиксируем стартовую точку
    el.style.transition = `transform .24s ${EASE}`;
    el.style.transform = "";
  };
}

const buzz = (ms) => { try { navigator.vibrate?.(ms); } catch { /* не везде есть */ } };

function makeSortable(list, onEnd) {
  if (!list) return;
  let row = null, startY = 0, moved = false, pid = null;
  const LIFT = " scale(1.025)";

  const move = (e) => {
    if (!row || e.pointerId !== pid) return;
    e.preventDefault();
    const gap = rowGap(list);
    for (let guard = 0; guard < 40; guard++) {
      const dy = e.clientY - startY;
      row.style.transform = `translateY(${dy}px)${LIFT}`;
      // решения принимаем по раскладке (offsetTop), а не по экрану: соседи
      // в этот момент могут ещё ехать, и их видимая позиция врёт
      const top = row.offsetTop + dy;
      const bottom = top + row.offsetHeight;
      const prev = row.previousElementSibling;
      const next = row.nextElementSibling;
      if (prev && top < prev.offsetTop + prev.offsetHeight / 2) {
        const done = flip(prev);
        startY -= prev.offsetHeight + gap;
        list.insertBefore(prev, row.nextElementSibling);   // соседа — под карточку
        done();
        moved = true;
        buzz(6);
        continue;
      }
      if (next && bottom > next.offsetTop + next.offsetHeight / 2) {
        const done = flip(next);
        startY += next.offsetHeight + gap;
        list.insertBefore(next, row);                      // соседа — над карточкой
        done();
        moved = true;
        buzz(6);
        continue;
      }
      break;
    }
  };

  const end = (e) => {
    if (!row || (e && e.pointerId !== pid)) return;
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    const el = row;
    // карточка «садится» на своё место, а не прыгает туда
    el.style.transition = `transform .26s ${EASE}, box-shadow .26s ${EASE}`;
    el.style.transform = "";
    el.classList.remove("dragging");
    el.classList.add("settling");
    const clean = () => {
      el.style.transition = "";
      el.classList.remove("settling");
      el.removeEventListener("transitionend", clean);
    };
    el.addEventListener("transitionend", clean);
    setTimeout(clean, 400);                        // на случай, если transitionend не придёт
    document.body.classList.remove("row-dragging");
    const order = $$("[data-open]", list).map((x) => x.dataset.open);
    row = null;
    pid = null;
    if (moved) onEnd(order);
  };

  list.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest("[data-drag]");
    if (!handle || row) return;
    row = handle.closest("[data-open]");
    if (!row) return;
    e.preventDefault();
    e.stopPropagation();
    pid = e.pointerId;
    startY = e.clientY;
    moved = false;
    // подъём карточки — анимируем, дальше она идёт за пальцем без задержки
    row.style.transition = `transform .16s ${EASE}, box-shadow .2s ${EASE}`;
    row.style.transform = `translateY(0px)${LIFT}`;
    row.classList.add("dragging");
    setTimeout(() => { if (row) row.style.transition = "box-shadow .2s"; }, 160);
    document.body.classList.add("row-dragging");
    buzz(12);
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });

  list.addEventListener("click", (e) => {
    if (e.target.closest("[data-drag]")) e.stopPropagation();
  }, true);
}

const rowGap = (tbody) => parseFloat(getComputedStyle(tbody).rowGap || "0") || 0;

async function saveOrder(path, order, what) {
  try { await post(path, { uuids: order }); toast(`Порядок ${what} сохранён`); }
  catch (e) { toast(`Порядок не сохранён: ${e.message}`, true); redraw(false); }
}

/* ---------- профили конфигов ---------- */
let profilesCache = null;
async function getProfiles(force) {
  if (!profilesCache || force) profilesCache = await api("/api/profiles");
  return profilesCache;
}

const optList = (items, current) => items.map((v) => {
  const [val, label] = Array.isArray(v) ? v : [v, v];
  return `<option value="${esc(val)}" ${String(val) === String(current ?? "") ? "selected" : ""}>${esc(label)}</option>`;
}).join("");

function profileSelect(profiles, current, name = "profile_uuid") {
  return `<label>Профиль конфига<select name="${name}" id="p-sel">
    <option value="">— не выбран —</option>
    ${optList(profiles.map((p) => [p.uuid, p.name]), current)}</select></label>`;
}

const inboundLabel = (i) =>
  `${i.tag}${i.port ? ` · :${i.port}` : ""}${i.type ? ` · ${i.type}` : ""}${i.security && i.security !== "none" ? ` · ${i.security}` : ""}`;

/* инбаунды профиля: чекбоксы (нода) либо выпадающий список (хост) */
function renderInbounds(profiles, wrap, { multi, selected }) {
  const uuid = $("#p-sel").value;
  const p = profiles.find((x) => x.uuid === uuid);
  if (!p) { wrap.innerHTML = `<span class="hint">Сначала выбери профиль конфига.</span>`; return; }
  if (!p.inbounds.length) { wrap.innerHTML = `<span class="hint">В профиле нет инбаундов.</span>`; return; }
  wrap.innerHTML = multi
    ? `<div class="checks">${p.inbounds.map((i) => `<label class="check"><input type="checkbox" name="inbounds"
        value="${esc(i.uuid)}" ${selected.includes(i.uuid) ? "checked" : ""}><span>${esc(inboundLabel(i))}</span></label>`).join("")}</div>`
    : `<select name="inbound_uuid"><option value="">— не выбран —</option>
        ${optList(p.inbounds.map((i) => [i.uuid, inboundLabel(i)]), selected[0])}</select>`;
}

function bindInbounds(profiles, opts) {
  const wrap = $("#inb-wrap");
  const draw = () => renderInbounds(profiles, wrap, opts);
  $("#p-sel").addEventListener("change", () => { opts.selected = []; draw(); });
  draw();
}

const GB = 1024 ** 3;
const COUNTRIES = ["XX", "NL", "DE", "FI", "SE", "LT", "LV", "EE", "PL", "FR", "GB", "US", "TR", "RU", "KZ", "AM", "JP", "SG", "HK"];

/* ---------- форма ноды ---------- */
async function nodeForm(existing) {
  const profiles = await getProfiles();
  const ssh = existing ? await api(`/api/nodes/${existing.uuid}/ssh`).catch(() => null) : null;
  const n = existing || { port: 2222, country_code: "XX", traffic_reset_day: 1, notify_percent: 0, consumption_multiplier: 1, inbounds: [] };
  const opts = { multi: true, selected: n.inbounds || [] };
  const el = openModal({
    title: existing ? `Нода: ${n.name}` : "Новая нода",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<div class="form-grid">
      <label>Название<input name="name" value="${esc(n.name || "")}" required maxlength="64"></label>
      <label>Адрес<input name="address" value="${esc(n.address || "")}" required placeholder="IP или домен">
        <span class="hint">Куда панель стучится к ноде</span></label>
      <label>Порт ноды<input name="port" type="number" min="1" max="65535" value="${n.port || 2222}"></label>
      <label>Страна<select name="country_code">${optList(COUNTRIES, n.country_code || "XX")}</select></label>
    </div>
    ${profileSelect(profiles, n.profile_uuid)}
    <label>Инбаунды<span class="hint">Отмеченные будут работать на этой ноде</span></label>
    <div id="inb-wrap"></div>
    <details class="more"><summary>Трафик и лимиты</summary><div class="form-grid">
      <label>Лимит трафика, ГБ<input name="limit_gb" type="number" min="0" step="1"
        value="${Math.round((n.traffic_limit_bytes || 0) / GB)}"><span class="hint">0 — без лимита</span></label>
      <label>День сброса<input name="traffic_reset_day" type="number" min="1" max="31" value="${n.traffic_reset_day || 1}"></label>
      <label>Уведомить при, %<input name="notify_percent" type="number" min="0" max="100" value="${n.notify_percent || 0}"></label>
      <label>Множитель расхода<input name="consumption_multiplier" type="number" min="0.1" max="100" step="0.1"
        value="${n.consumption_multiplier || 1}"></label>
      <label class="check"><input type="checkbox" name="traffic_tracking" ${n.traffic_tracking ? "checked" : ""}>
        <span>Считать трафик ноды</span></label>
    </div></details>
    <details class="more" ${ssh ? "open" : ""}><summary>SSH-доступ к серверу ноды${ssh ? " · подключён" : ""}</summary>
      <p class="hint" style="margin:0 0 12px">Нужен для обновления ноды, Hysteria2 и CDN — по API Remnawave это недоступно.
        Можно заполнить позже, из карточки ноды.</p>
      ${sshFields(ssh || { host: n.address || "" }, !!ssh)}
      ${existing ? "" : `<label class="check" style="margin-top:4px"><input type="checkbox" name="ssh_install" id="f-install">
        <span>Установить ноду на этот сервер</span></label>
      <label class="check"><input type="checkbox" name="ssh_tune"><span>Заодно включить BBR и поднять лимиты</span></label>
      <p class="hint" style="margin:8px 0 0">Панель поставит docker, положит ноду в указанный каталог,
        пропишет ключ этой панели и запустит контейнер. Сервер должен быть чистым: если нода там уже есть,
        установка остановится и ничего не тронет.</p>`}
    </details>`,
    onSubmit: async (v, form) => {
      const payload = {
        name: v.name, address: v.address, port: Number(v.port) || 2222,
        country_code: v.country_code, profile_uuid: v.profile_uuid || "",
        inbounds: $$("input[name=inbounds]:checked", form).map((i) => i.value),
        traffic_limit_bytes: Math.max(0, Number(v.limit_gb) || 0) * GB,
        traffic_reset_day: Number(v.traffic_reset_day) || 1,
        notify_percent: Number(v.notify_percent) || 0,
        consumption_multiplier: Number(v.consumption_multiplier) || 1,
        traffic_tracking: !!v.traffic_tracking,
      };
      let uuid = existing?.uuid;
      if (existing) await api(`/api/nodes/${uuid}`, { method: "PUT", body: JSON.stringify(payload) });
      else uuid = (await post("/api/nodes", payload)).uuid;
      const sp = sshPayload(v);
      if (uuid && sp.host && (sp.secret || ssh)) {
        try {
          await api(`/api/nodes/${uuid}/ssh`, {
            method: "PUT", body: JSON.stringify({ ...sp, name: payload.name }),
          });
        } catch (x) { toast(`Нода сохранена, но SSH-доступ не записан: ${x.message}`, true); }
      } else if (sp.host && !sp.secret && !ssh) {
        toast("Нода сохранена. SSH-доступ не записан: не указан пароль или ключ", true);
      }
      if (v.ssh_install && uuid && sp.host && sp.secret) {
        try {
          const r = await post(`/api/nodes/${uuid}/ssh/install`, { tune: !!v.ssh_tune, force: false });
          jobConsole(r.job, `Установка ноды · ${payload.name}`);
        } catch (x) { toast(`Нода создана, но установка не запустилась: ${x.message}`, true); }
      } else {
        toast(existing ? "Нода сохранена" : "Нода создана");
      }
      redraw(false);
    },
  });
  bindInbounds(profiles, opts);
  bindAuth(el, !!ssh);
}

/* ---------- форма хоста ---------- */
const ALPN = ["", "h2", "http/1.1", "h2,http/1.1"];
const FP = ["", "chrome", "firefox", "safari", "ios", "android", "edge", "random", "randomized"];
const SEC = [["DEFAULT", "как в инбаунде"], ["TLS", "TLS"], ["NONE", "без TLS"]];

async function hostForm(existing) {
  const profiles = await getProfiles();
  const h = existing || { port: 443, security_layer: "DEFAULT" };
  const opts = { multi: false, selected: [h.inbound_uuid || ""] };
  openModal({
    title: existing ? `Хост: ${h.remark}` : "Новый хост",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<div class="form-grid">
      <label>Название в подписке<input name="remark" value="${esc(h.remark || "")}" required maxlength="64"></label>
      <label>Адрес<input name="address" value="${esc(h.address || "")}" required placeholder="домен или IP"></label>
      <label>Порт<input name="port" type="number" min="1" max="65535" value="${h.port || 443}"></label>
      <label>Слой безопасности<select name="security_layer">${optList(SEC, h.security_layer || "DEFAULT")}</select></label>
    </div>
    ${profileSelect(profiles, h.profile_uuid)}
    <label>Инбаунд<span class="hint">Хост отдаёт клиенту параметры этого инбаунда</span></label>
    <div id="inb-wrap"></div>
    <details class="more" ${h.path || h.sni || h.host ? "open" : ""}><summary>Транспорт и маскировка</summary>
      <div class="form-grid">
        <label>Path<input name="path" value="${esc(h.path || "")}" placeholder="/api/v4/media/…"></label>
        <label>SNI<input name="sni" value="${esc(h.sni || "")}"></label>
        <label>Host<input name="host" value="${esc(h.host || "")}"></label>
        <label>ALPN<select name="alpn">${optList(ALPN.map((a) => [a, a || "— не задан —"]), h.alpn || "")}</select></label>
        <label>Fingerprint<select name="fingerprint">${optList(FP.map((a) => [a, a || "— не задан —"]), h.fingerprint || "")}</select></label>
        <label>Подпись под названием<input name="server_description" value="${esc(h.server_description || "")}"
          placeholder="например: Финляндия · 10 Гбит/с"></label>
      </div>
      <label class="check"><input type="checkbox" name="allow_insecure" ${h.allow_insecure ? "checked" : ""}>
        <span>Разрешить недоверенный сертификат</span></label>
      <label class="check"><input type="checkbox" name="disabled" ${h.disabled ? "checked" : ""}>
        <span>Выключен (не попадает в подписку)</span></label>
    </details>
    <span class="hint">Кастомный xhttp <code>extra</code> задаётся в инбаунде профиля — панель сама раздаёт его хостам.</span>`,
    onSubmit: async (v) => {
      const payload = {
        remark: v.remark, address: v.address, port: Number(v.port) || 443,
        profile_uuid: v.profile_uuid || "", inbound_uuid: v.inbound_uuid || "",
        path: v.path || "", sni: v.sni || "", host: v.host || "", alpn: v.alpn || "",
        fingerprint: v.fingerprint || "", security_layer: v.security_layer || "DEFAULT",
        server_description: v.server_description || "",
        allow_insecure: !!v.allow_insecure, disabled: !!v.disabled,
      };
      if (existing) await api(`/api/hosts/${existing.uuid}`, { method: "PUT", body: JSON.stringify(payload) });
      else await post("/api/hosts", payload);
      toast(existing ? "Хост сохранён" : "Хост создан");
      redraw(false);
    },
  });
  bindInbounds(profiles, opts);
}

/* ---------- SSH: общие поля для всех форм ---------- */
const sshFields = (s = {}, isEdit = false) => `<div class="form-grid">
  <label>Хост SSH<input name="ssh_host" value="${esc(s.host || "")}" placeholder="IP или домен сервера ноды"></label>
  <label>Порт SSH<input name="ssh_port" type="number" min="1" max="65535" value="${s.port || 22}"></label>
  <label>Пользователь<input name="ssh_username" value="${esc(s.username || "root")}">
    <span class="hint">Не root — нужен sudo без пароля</span></label>
  <label>Способ входа<select name="ssh_auth" class="auth-sel">
    ${optList([["password", "Пароль"], ["key", "Приватный ключ"]], s.auth || "password")}</select></label>
</div>
<label class="secret-l">${isEdit ? "Новый пароль" : "Пароль"}
  <input name="ssh_secret" type="password" autocomplete="new-password"
    ${isEdit ? 'placeholder="оставь пустым, чтобы не менять"' : ""}></label>
<label class="keypass-l" hidden>Пароль ключа (если есть)
  <input name="ssh_key_pass" type="password" autocomplete="off"></label>
<span class="hint key-hint" hidden>Нужен приватный ключ целиком — файл <code>id_ed25519</code> <b>без</b> .pub,
  от строки <code>-----BEGIN OPENSSH PRIVATE KEY-----</code> до <code>-----END…-----</code>.
  Строка вида <code>ssh-ed25519 AAAA…</code> — это публичный ключ, он лежит на сервере
  в <code>~/.ssh/authorized_keys</code> и для входа не подходит.</span>
<label>Каталог ноды<input name="ssh_node_path" value="${esc(s.node_path || "/opt/remnanode")}">
  <span class="hint">Там лежит docker-compose.yml ноды</span></label>`;

/* переключение «пароль ↔ ключ»: у ключа нужно многострочное поле */
function bindAuth(el, isEdit) {
  const sel = $(".auth-sel", el);
  if (!sel) return;
  const swap = () => {
    const key = sel.value === "key";
    const field = $("[name=ssh_secret]", el);
    $(".keypass-l", el).hidden = !key;
    const hint = $(".key-hint", el);
    if (hint) hint.hidden = !key;
    $(".secret-l", el).firstChild.textContent = key
      ? (isEdit ? "Новый приватный ключ" : "Приватный ключ")
      : (isEdit ? "Новый пароль" : "Пароль");
    const ph = isEdit ? "оставь пустым, чтобы не менять" : (key ? "-----BEGIN OPENSSH PRIVATE KEY-----" : "");
    if (key && field.tagName === "INPUT") {
      const ta = document.createElement("textarea");
      ta.name = "ssh_secret"; ta.rows = 5; ta.placeholder = ph; ta.spellcheck = false;
      field.replaceWith(ta);
    } else if (!key && field.tagName === "TEXTAREA") {
      const inp = document.createElement("input");
      inp.name = "ssh_secret"; inp.type = "password"; inp.placeholder = ph;
      field.replaceWith(inp);
    }
  };
  sel.addEventListener("change", swap);
  swap();
}

const sshPayload = (v) => ({
  host: (v.ssh_host || "").trim(), port: Number(v.ssh_port) || 22,
  username: (v.ssh_username || "").trim() || "root", auth: v.ssh_auth || "password",
  secret: (v.ssh_secret || "").trim(), key_pass: v.ssh_key_pass || "",
  node_path: (v.ssh_node_path || "").trim() || "/opt/remnanode",
});

/* SSH-доступ конкретной ноды */
function sshForm(node, existing, after) {
  const s = existing || { host: node.address, name: node.name };
  const el = openModal({
    title: `SSH-доступ · ${node.name}`,
    wide: true,
    submit: existing ? "Сохранить" : "Подключить",
    body: `<p class="muted" style="margin:0">Обновление ноды, Hysteria2 и CDN выполняются на самом сервере —
      через API Remnawave туда не попасть. Панель зайдёт по SSH из своего контейнера.</p>
      ${sshFields(s, !!existing)}
      <label>Заметка<input name="note" value="${esc(s.note || "")}"></label>
      <span class="hint">Пароль и ключ шифруются ключом <code>CRYPT_KEY</code> из .env, лежат в
        <code>data/panel.db</code> и ни в одном ответе API наружу не отдаются.</span>`,
    onSubmit: async (v) => {
      const p = sshPayload(v);
      if (!p.host) throw new Error("Укажи хост сервера");
      if (!existing && !p.secret) throw new Error("Нужен пароль или приватный ключ");
      await api(`/api/nodes/${node.uuid}/ssh`, {
        method: "PUT", body: JSON.stringify({ ...p, name: node.name, note: v.note || "" }),
      });
      toast(existing ? "Доступ сохранён" : "Сервер подключён");
      if (after) after();
      redraw(false);
    },
  });
  bindAuth(el, !!existing);
}

/* ---------- форма сервера (раздел «Серверы») ---------- */
function serverForm(existing, nodes, preset = {}) {
  const s = existing || { port: 22, username: "root", node_path: "/opt/remnanode", auth: "password", ...preset };
  const el = openModal({
    title: existing ? `Сервер: ${s.name}` : "Новый сервер",
    wide: true,
    submit: existing ? "Сохранить" : "Добавить",
    body: `<label>Название<input name="name" value="${esc(s.name || "")}" required maxlength="48"></label>
      ${sshFields(s, !!existing)}
      <label>Нода в Remnawave<select name="node_uuid">
        <option value="">— не привязана —</option>
        ${optList(nodes.map((n) => [n.uuid, `${n.name} (${n.address})`]), s.node_uuid)}</select>
        <span class="hint">Привязка добавляет кнопки обновления и настройки в карточку ноды</span></label>
      <label>Заметка<input name="note" value="${esc(s.note || "")}"></label>
      <span class="hint">Пароль и ключ шифруются ключом <code>CRYPT_KEY</code> из .env и наружу не отдаются.</span>`,
    onSubmit: async (v) => {
      const payload = { ...sshPayload(v), name: v.name, node_uuid: v.node_uuid || "", note: v.note || "" };
      if (!payload.host) throw new Error("Укажи хост сервера");
      if (existing) await api(`/api/servers/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await post("/api/servers", payload);
      toast(existing ? "Сервер сохранён" : "Сервер добавлен");
      redraw(false);
    },
  });
  bindAuth(el, !!existing);
}

/* ---------- разделы ---------- */
const serverPill = (srv) => srv
  ? `<span class="sub">SSH: ${esc(srv.name)}</span>`
  : `<span class="sub muted">сервер не привязан</span>`;

Object.assign(VIEWS, {
  nodes: {
    title: "Ноды", sub: "", refresh: 15, bare: true,
    load: async () => {
      const [nodes, servers, sites, templates] = await Promise.all([
        api("/api/nodes"), api("/api/servers").catch(() => []),
        api("/api/cdn/sites").catch(() => []), api("/api/cdn/templates").catch(() => [])]);
      return { nodes, servers, sites, templates };
    },
    actions: () => "",
    draw: (d) => {
      const up = d.nodes.filter((n) => n.state === 1);
      const rx = up.reduce((a, n) => a + (n.rx_bps || 0), 0);
      const tx = up.reduce((a, n) => a + (n.tx_bps || 0), 0);
      const online = up.reduce((a, n) => a + (n.online || 0), 0);
      const byUuid = Object.fromEntries(d.servers.filter((s) => s.node_uuid).map((s) => [s.node_uuid, s]));
      const q = nodesView.q.trim().toLowerCase();
      const list = q ? d.nodes.filter((n) => `${n.name} ${n.address} ${n.country}`.toLowerCase().includes(q)) : d.nodes;

      return `<div class="grid kpis">
          ${kpiCard("green", "down", "Скачивание", speedKpi(rx), "по всем нодам")}
          ${kpiCard("blue", "up", "Отдача", speedKpi(tx), "по всем нодам")}
          ${kpiCard("cyan", "users", "Онлайн", `<span data-count="${online}">${online}</span>`, "пользователей")}
          ${kpiCard(up.length === d.nodes.length ? "green" : "amber", "pulse", "Нод на связи",
            `<span data-count="${up.length}">${up.length}</span><small> / ${d.nodes.length}</small>`,
            up.length === d.nodes.length ? "все на связи" : `${d.nodes.length - up.length} без связи`)}
        </div>

        <div class="panel list-head" data-a>
          <div class="list-title"><span class="row-tile big-tile">${icon("server")}</span><h2>Ноды</h2></div>
          <div class="tool-row">
            <button class="tool-btn" id="n-search" aria-label="Поиск">${icon("search")}</button>
            <button class="tool-btn ${nodesView.compact ? "on" : ""}" id="n-compact" aria-label="Компактный вид">${icon("table")}</button>
            <button class="tool-btn violet" id="n-update-all" aria-label="Обновить все ноды">${icon("download")}</button>
            <button class="tool-btn cyan" data-refresh aria-label="Обновить список">${icon("refresh")}</button>
            <button class="tool-btn green" id="n-add" aria-label="Добавить ноду">${icon("plus")}</button>
          </div>
          <div class="list-search" ${nodesView.searchOpen || q ? "" : "hidden"}>
            <div class="search">${icon("search")}<input id="n-q" type="search" placeholder="Имя, адрес или страна"
              value="${esc(nodesView.q)}"></div>
          </div>
        </div>

        ${list.length ? `<div class="cards ${nodesView.compact ? "compact" : ""}" id="node-cards">
          ${list.map((n) => nodeCard(n, byUuid[n.uuid])).join("")}</div>`
          : `<div class="panel"><p class="empty">${d.nodes.length ? "Ничего не нашлось." : "Нод пока нет. Нажми «+» — панель заведёт её в Remnawave."}</p></div>`}`;
    },
    bind: (d, root) => {
      const byUuid = Object.fromEntries(d.servers.filter((s) => s.node_uuid).map((s) => [s.node_uuid, s]));
      $("[data-refresh]", root)?.addEventListener("click", (e) => busy(e.currentTarget, () => redraw(false)));
      $("#n-add").addEventListener("click", () => nodeForm(null));
      $("#n-compact").addEventListener("click", () => { nodesView.compact = !nodesView.compact; redraw(false); });
      $("#n-search").addEventListener("click", () => {
        nodesView.searchOpen = !nodesView.searchOpen;
        const box = $(".list-search", root);
        box.hidden = !nodesView.searchOpen && !nodesView.q;
        if (!box.hidden) $("#n-q", root).focus();
      });
      let deb;
      $("#n-q", root)?.addEventListener("input", (e) => {
        clearTimeout(deb);
        deb = setTimeout(() => { nodesView.q = e.target.value; redraw(false).then(() => {
          const inp = $("#n-q"); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
        }); }, 250);
      });
      $("#n-update-all").addEventListener("click", () => {
        if (!d.servers.length) return toast("Сначала укажи SSH-доступ хотя бы у одной ноды", true);
        confirmModal("Обновить все ноды?",
          `Панель по очереди зайдёт на ${d.servers.length} сервер(ов) и выполнит docker compose pull и up -d. Ноды на время перезапуска отвалятся.`,
          async () => { const r = await post("/api/servers/update-all"); jobConsole(r.job, "Обновление всех нод"); },
          "Обновить");
      });
      // при поиске порядок частичный — перетаскивание отключаем, чтобы не перемешать скрытые ноды
      if (!nodesView.q) makeSortable($("#node-cards", root), (order) => saveOrder("/api/nodes-reorder", order, "нод"));
      bindCards(root, (uuid) => {
        const n = d.nodes.find((x) => x.uuid === uuid);
        if (n) nodeSheet(n, byUuid[uuid], d);
      });
    },
  },

  hosts: {
    title: "Хосты", sub: "Порядок как в Remnawave — тяни за ручку справа", refresh: 0,
    load: () => api("/api/hosts"),
    actions: (rows) => `<span class="muted" style="font-size:13px">${rows.filter((r) => !r.disabled).length} из ${rows.length} включены</span>
      <button class="btn btn-sm" id="h-reach">${icon("globe")}Из России</button>
      <button class="btn btn-sm btn-primary" id="h-add">Добавить хост</button>${refreshBtn}`,
    draw: (rows) => rows.length ? `<div class="cards" data-a id="host-cards">${rows.map((h) => `
      <article class="card-row ${h.disabled ? "off" : "up"}" data-open="${h.uuid}" tabindex="0" role="button"
        aria-label="Открыть хост ${esc(h.remark)}">
        <header>
          <span class="row-tile">${icon("link")}</span>
          <b class="name">${esc(h.remark)}</b>
          <span class="grip" data-drag title="Перетащи, чтобы изменить порядок">
            <svg viewBox="0 0 24 24"><path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01" stroke-width="2.6"/></svg></span>
        </header>
        <p class="addr mono">${esc(h.address)}:${h.port}</p>
        <footer>
          ${h.inbound_tag ? `<span class="pill accent">${esc(h.inbound_tag)}</span>` : ""}
          ${h.security_layer && h.security_layer !== "DEFAULT" ? `<span class="pill">${esc(h.security_layer)}</span>` : ""}
          ${h.path ? `<span class="pill">path ${esc(h.path)}</span>` : ""}
          ${h.disabled ? `<span class="tag off">выключен</span>` : `<span class="tag ok">включён</span>`}
        </footer>
        ${h.server_description ? `<p class="row-note">${esc(h.server_description)}</p>` : ""}
      </article>`).join("")}</div>`
      : `<div class="panel" data-a><p class="empty">Хостов нет. Хост — это то, что видит клиент: адрес, порт и параметры инбаунда.</p></div>`,
    bind: (rows, root) => {
      bindRefresh(root);
      $("#h-add").addEventListener("click", () => hostForm(null));
      $("#h-reach")?.addEventListener("click", () => {
        const t = [...new Set(rows.filter((h) => !h.disabled).map((h) => `${h.address}:${h.port}`))];
        if (!t.length) return toast("Нет включённых хостов", true);
        if (t.length > 20) toast(`Проверю первые 20 адресов из ${t.length}`);
        reachCheck(t.slice(0, 20), "Хосты из России");
      });
      makeSortable($("#host-cards", root), (order) => saveOrder("/api/hosts-reorder", order, "хостов"));
      bindCards(root, (uuid) => { const h = rows.find((x) => x.uuid === uuid); if (h) hostSheet(h); });
    },
  },

  servers: {
    title: "Серверы", sub: "", bare: true,
    load: async () => {
      const [servers, nodes] = await Promise.all([api("/api/servers"), api("/api/nodes").catch(() => [])]);
      return { servers, nodes };
    },
    actions: () => "",
    draw: (d) => {
      const failed = (s) => s.last_fail && (!s.last_ok || s.last_fail > s.last_ok);
      const ok = d.servers.filter((s) => s.last_ok && !failed(s)).length;
      const bad = d.servers.filter(failed).length;
      const never = d.servers.length - ok - bad;
      return `<div class="grid kpis">
          ${kpiCard("cyan", "ssh", "Серверов", `<span data-count="${d.servers.length}">${d.servers.length}</span>`, "с SSH-доступом")}
          ${kpiCard("green", "check", "Доступны", `<span data-count="${ok}">${ok}</span>`, "по последней проверке")}
          ${kpiCard(bad ? "red" : "muted", "unlink", "Нет доступа", `<span data-count="${bad}">${bad}</span>`, bad ? "смотри карточки" : "всё ок")}
          ${kpiCard(never ? "amber" : "muted", "clock", "Не проверялись", `<span data-count="${never}">${never}</span>`, never ? "нажми «Проверить все»" : "—")}
        </div>
        <div class="panel list-head" data-a>
          <div class="list-title"><span class="row-tile big-tile">${icon("ssh")}</span><h2>Серверы</h2></div>
          <div class="tool-row">
            <button class="tool-btn cyan" id="s-check-all" aria-label="Проверить SSH у всех" ${d.servers.length ? "" : "disabled"}>${icon("check")}</button>
            <button class="tool-btn violet" id="s-update-all" aria-label="Обновить все ноды" ${d.servers.length ? "" : "disabled"}>${icon("download")}</button>
            <button class="tool-btn" data-refresh aria-label="Обновить список">${icon("refresh")}</button>
            <button class="tool-btn green" id="s-add" aria-label="Добавить сервер">${icon("plus")}</button>
          </div>
        </div>
        ${d.servers.length ? `<div class="cards">${d.servers.map((s) => {
          const node = d.nodes.find((n) => n.uuid === s.node_uuid);
          const tone = failed(s) ? "down" : s.last_ok ? "up" : "";
          return `<article class="card-row ${tone}" data-open="${s.id}" tabindex="0" role="button" aria-label="Открыть сервер ${esc(s.name)}">
            <header>
              <span class="row-tile">${icon("ssh")}</span>
              ${node ? `<span class="flag" aria-hidden="true">${flagOf(node.country)}</span>` : ""}
              <b class="name">${esc(s.name)}</b>
              ${failed(s) ? `<span class="tag bad">нет доступа</span>`
                : s.last_ok ? `<span class="tag ok">${ago(s.last_ok)}</span>` : `<span class="tag warn">не проверялся</span>`}
            </header>
            <p class="addr mono">${esc(s.username)}@${esc(s.host)}:${s.port}</p>
            <div class="row-meta">
              <span class="pill">${s.auth === "key" ? "ключ" : "пароль"}</span>
              ${node ? `<span class="pill accent">нода ${esc(node.name)}</span>` : `<span class="pill">без ноды</span>`}
            </div>
            ${failed(s) && s.last_error ? `<p class="row-err">${esc(s.last_error.slice(0, 140))}</p>`
              : s.last_info ? `<p class="row-note">${esc(s.last_info.slice(0, 140))}</p>` : ""}
          </article>`;
        }).join("")}</div>`
        : `<div class="panel"><p class="empty">Серверов нет. Добавь SSH-доступ здесь или прямо в карточке ноды.</p></div>`}`;
    },
    bind: (d, root) => {
      $("[data-refresh]", root)?.addEventListener("click", (e) => busy(e.currentTarget, () => redraw(false)));
      $("#s-add").addEventListener("click", () => serverForm(null, d.nodes));
      $("#s-check-all").addEventListener("click", (e) => busy(e.currentTarget, async () => {
        toast(`Проверяю ${d.servers.length} сервер(ов)…`);
        try { checkReport(await post("/api/servers-check-all")); }
        catch (x) { toast(x.message, true); }
        redraw(false);
      }));
      $("#s-update-all").addEventListener("click", () => confirmModal("Обновить все ноды?",
        `Панель по очереди зайдёт на ${d.servers.length} сервер(ов).`,
        async () => { const r = await post("/api/servers/update-all"); jobConsole(r.job, "Обновление всех нод"); }, "Обновить"));
      bindCards(root, (id) => serverSheet(d.servers.find((x) => String(x.id) === id), d));
    },
  },

  cdn: {
    title: "CDN", sub: "nginx перед нодой: домен, сертификат и путь до инбаунда",
    load: async () => {
      const [sites, templates, servers] = await Promise.all([
        api("/api/cdn/sites"), api("/api/cdn/templates"), api("/api/servers")]);
      return { sites, templates, servers };
    },
    actions: (d) => `<button class="btn btn-sm" id="t-add">Шаблон nginx</button>
      <button class="btn btn-sm btn-primary" id="c-add" ${d.servers.length && d.templates.length ? "" : "disabled"}>Настроить CDN</button>${refreshBtn}`,
    draw: (d) => `
      <div class="panel" data-a><h2>Настроенные домены</h2>
      ${d.sites.length ? `<div class="table-wrap"><table class="rows">
        <thead><tr><th>Домен</th><th>Сервер</th><th>Шаблон</th><th>Путь</th><th></th></tr></thead>
        <tbody>${d.sites.map((s) => `<tr data-site="${s.id}" tabindex="0" role="button" aria-label="Открыть ${esc(s.domain)}">
          <td><b>${esc(s.domain)}</b>${(s.domains || "").split(" ").length > 1
            ? `<span class="sub">+ ${esc((s.domains || "").split(" ").slice(1).join(", "))}</span>` : ""}
            <span class="sub">${ago(s.ts)}${s.is_default_server ? " · default_server" : ""}</span></td>
          <td class="muted" data-l="Сервер">${esc(s.server_name || "—")}</td>
          <td class="muted" data-l="Шаблон">${esc(s.template_name || "—")}</td>
          <td class="muted" data-l="Путь">${esc(s.path)} → :${s.upstream}</td>
          <td class="chev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="empty">Пока ни одного. Нажми «Настроить CDN»: панель поставит nginx, выпустит сертификат и включит конфиг.</p>`}
      </div>
      <div class="panel" data-a><h2>Шаблоны nginx</h2>
        <p class="hint" style="margin:-4px 0 14px">В конфиге подставляются <code>{{server_names}}</code>,
          <code>{{path}}</code>, <code>{{port}}</code>, <code>{{upstream}}</code>, <code>{{cert}}</code>, <code>{{key}}</code>.</p>
        <div class="table-wrap"><table class="rows"><thead><tr><th>Шаблон</th><th>Путь по умолчанию</th><th>Порт</th><th></th></tr></thead>
        <tbody>${d.templates.map((t) => `<tr data-tpl="${t.id}" tabindex="0" role="button" aria-label="Открыть шаблон ${esc(t.name)}">
          <td><b>${esc(t.name)}</b><span class="sub">${esc(t.slug)}${t.builtin ? " · встроенный" : ""}</span></td>
          <td class="muted" data-l="Путь">${esc(t.default_path || "/")}</td>
          <td class="muted" data-l="Порт">${t.default_port || 10085}${t.is_default_server ? " · default_server" : ""}</td>
          <td class="chev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></td></tr>`).join("")}</tbody></table></div></div>`,
    bind: (d, root) => {
      bindRefresh(root);
      $("#t-add").addEventListener("click", () => templateForm(null));
      $("#c-add").addEventListener("click", () => siteForm(d));
      const rowOpen = (attr, fn) => $$(`tr[data-${attr}]`, root).forEach((tr) => {
        const go = () => fn(tr.dataset[attr]);
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      });
      rowOpen("site", (id) => siteSheet(d.sites.find((x) => String(x.id) === id), d));
      rowOpen("tpl", (id) => templateSheet(d.templates.find((x) => String(x.id) === id)));
    },
  },

  jobs: {
    title: "Задачи", sub: "Что панель делала на серверах по SSH", refresh: 10,
    load: () => api("/api/jobs"),
    actions: () => refreshBtn,
    draw: (rows) => `<div class="panel" data-a>${rows.length ? `<div class="table-wrap"><table class="rows">
      <thead><tr><th>Задача</th><th>Статус</th><th>Запущена</th><th></th></tr></thead>
      <tbody>${rows.map((j) => `<tr data-job="${j.id}" data-title="${esc(j.title)}" tabindex="0" role="button">
        <td><b>${esc(j.title)}</b><span class="sub">${esc(j.target || "")}</span></td>
        <td><span class="tag ${j.status === "ok" ? "ok" : j.status === "error" ? "bad" : "info"}">
          ${j.status === "ok" ? "готово" : j.status === "error" ? "ошибка" : "выполняется"}</span></td>
        <td class="muted" data-l="Запущена">${ago(j.started)}</td>
        <td class="chev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></td>
      </tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Задач ещё не было. Они появляются при обновлении нод и настройке CDN или Hysteria2.</p>`}</div>`,
    bind: (rows, root) => {
      bindRefresh(root);
      $$("[data-job]", root).forEach((tr) => {
        const go = () => jobConsole(tr.dataset.job, tr.dataset.title);
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      });
    },
  },
});

/* ---------- карточка ноды ---------- */
const kvRow = (k, v) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`;

/* секция-подкарточка с плиткой и заголовком, как в Remnawave */
const secCard = (ico, title, inner, tone = "", sub = "") => `<section class="sec-card ${tone}">
  <div class="sec-head"><span class="sec-tile">${icon(ico)}</span>
    <span class="sec-titles"><h4>${esc(title)}</h4>${sub ? `<span class="sec-sub">${esc(sub)}</span>` : ""}</span></div>
  ${inner}</section>`;

/* плитка-кнопка: иконка сверху, короткая подпись снизу */
const tileBtn = (a, ico, label, tone = "") =>
  `<button type="button" class="tile-btn ${tone}" data-a="${a}">${icon(ico)}<span>${label}</span></button>`;
const tiles = (...items) => `<div class="tile-grid">${items.join("")}</div>`;

const statChip = (ico, value, tone = "") => `<span class="stat-chip ${tone}">${icon(ico)}<b>${value}</b></span>`;

function nodeSheet(n, srv, d) {
  const sites = srv ? (d.sites || []).filter((s) => s.server_id === srv.id) : [];
  const tone = n.state === 1 ? "up" : n.state === -1 ? "off" : "down";
  const pct = n.traffic_limit ? Math.min(100, n.traffic / n.traffic_limit * 100) : null;

  const details = secCard("pulse", "Подробности", `
    <div class="meter"><b class="big">${fmtBytes(n.traffic)}</b>
      <span class="lim">${pct === null ? "без лимита"
        : `${Math.round(pct)}% из ${fmtBytes(n.traffic_limit)}`}</span></div>
    ${pct === null ? "" : `<div class="bar"><i style="width:${Math.max(pct, 2)}%"></i></div>`}
    <div class="chip-grid">
      ${statChip("users", n.online, "green")}
      ${n.xray ? statChip("tool", `Xray ${esc(n.xray)}`, "violet") : ""}
      ${n.node_version ? statChip("server", `Нода ${esc(n.node_version)}`, "blue") : ""}
      ${n.state === 1 ? statChip("clock", `аптайм ${uptime(n.uptime)}`, "green") : ""}
      ${n.state === 1 && n.mem_pct != null ? statChip("ram", `память ${n.mem_pct}% · ${fmtBytes(n.mem_used)} из ${fmtBytes(n.mem_total)}`) : ""}
      ${n.state === 1 ? statChip("up", `${speed(n.tx_bps)}`, "blue") + statChip("down", `${speed(n.rx_bps)}`, "green") : ""}
      ${statChip("server", esc(n.country || "—") + " · " + esc(n.address), "blue")}
      ${srv ? statChip("ssh", esc(srv.name), "cyan") : ""}
    </div>`, tone);

  const server = srv ? secCard("ssh", "Сервер", `
      ${tiles(
        tileBtn("term", "term", "Терминал", "cyan"),
        tileBtn("logs", "logs", "Лог ноды", "blue"),
        tileBtn("update", "download", "Обновить ноду", "green"),
        tileBtn("hy2", "bolt", "Hysteria2", "violet"),
        tileBtn("cdn-add", "cloud", "Настроить CDN", "blue"),
        tileBtn("cdn-status", "pulse", "Состояние CDN", "cyan"),
        tileBtn("install", "install", "Установить"),
        tileBtn("ssh-check", "check", "Проверить SSH"),
        tileBtn("srv-edit", "key", "Доступ"),
        tileBtn("ssh-drop", "unlink", "Отвязать", "red"))}
      ${srv.last_info ? `<p class="hint" style="margin:12px 0 0">${esc(srv.last_info)}</p>` : ""}
      ${sites.length ? `<div class="mini-list">${sites.map((x) => `<div class="mini-row">
          <span><b>${esc(x.domain)}</b><span class="sub">${esc(x.path)} → 127.0.0.1:${x.upstream}</span></span>
          <button class="btn btn-sm btn-danger" data-cdn-del="${x.id}" data-name="${esc(x.domain)}">Снять</button>
        </div>`).join("")}</div>` : ""}`, "", `${srv.username}@${srv.host}:${srv.port}`)
    : secCard("ssh", "SSH-доступ не указан", `
        <p class="hint" style="margin:0 0 12px">Обновление ноды, Hysteria2 и CDN делаются на самом сервере:
          в API Remnawave таких методов нет.</p>
        <div class="action-grid"><button class="btn btn-primary" data-a="ssh-add">Указать SSH-доступ</button></div>`);

  const sheet = openSheet({
    title: `<span class="flag">${flagOf(n.country)}</span> ${esc(n.name)}`,
    lead: icon(n.state === 1 ? "pulse" : "server"),
    uuid: n.uuid,
    tag: stateTag(n.state),
    subtitle: n.state === 0 && n.message ? esc(String(n.message).slice(0, 160)) : "",
    body: `${details}
      ${secCard("grid", "Нода в Remnawave", tiles(
        tileBtn("reach", "globe", "Из России", "cyan"),
        tileBtn("edit", "edit", "Изменить"),
        tileBtn("restart", "refresh", "Перезапуск"),
        tileBtn("reset-traffic", "reset", "Сброс трафика"),
        n.state === -1 ? tileBtn("enable", "power", "Включить", "green")
                       : tileBtn("disable", "power", "Выключить", "amber"),
        tileBtn("delete", "trash", "Удалить", "red")))}
      ${server}`,
  });

  const act = (name, fn) => $(`[data-a="${name}"]`, sheet)?.addEventListener("click", fn);

  act("edit", (e) => busy(e.currentTarget, async () => {
    try { const full = await api(`/api/nodes/${n.uuid}/full`); sheet.close(); nodeForm(full); }
    catch (x) { toast(x.message, true); }
  }));
  const simple = (action, title, text) => act(action, () => {
    const go = () => busy($(`[data-a="${action}"]`, sheet), async () => {
      try { await post(`/api/nodes/${n.uuid}/${action}`); toast("Команда отправлена"); sheet.close(); setTimeout(() => redraw(false), 1500); }
      catch (x) { toast(x.message, true); }
    });
    if (!text) return go();
    confirmModal(title, text, go, title.replace(" ноду?", ""));
  });
  simple("restart", "Перезапустить ноду?", "Клиенты на ней потеряют соединение.");
  act("reach", () => reachCheck([`${n.address}:443`], `Из России · ${n.name}`));
  simple("disable", "Выключить ноду?", "Нода пропадёт из подписок, клиенты уйдут на другие.");
  simple("enable", "Включить ноду?", "");
  simple("reset-traffic", "Сбросить трафик ноды?", "Счётчик обнулится, история использования останется.");
  act("delete", () => confirmModal(`Удалить ноду ${n.name}?`,
    "Запись исчезнет из Remnawave. Контейнер на сервере продолжит работать — его нужно останавливать отдельно.",
    async () => { await api(`/api/nodes/${n.uuid}`, { method: "DELETE" }); toast("Нода удалена"); sheet.close(); redraw(false); }));

  if (srv) {
    act("update", () => confirmModal(`Обновить ноду на ${srv.name}?`, "docker compose pull и up -d. Нода перезапустится.",
      async () => { const r = await post(`/api/nodes/${n.uuid}/ssh/update`); sheet.close(); jobConsole(r.job, `Обновление · ${n.name}`); }, "Обновить"));
    act("hy2", () => { sheet.close(); hy2Form(n.uuid, n.name, true); });
    act("term", () => { sheet.close(); openTerminal(srv); });
    act("logs", () => { sheet.close(); openLogs(srv); });
    act("cdn-status", (e) => runJob(e.currentTarget, `/api/nodes/${n.uuid}/ssh/cdn-status`, {}, `Состояние CDN · ${n.name}`));
    act("cdn-add", () => {
      if (!d.templates.length) return toast("Сначала добавь шаблон nginx в разделе «CDN»", true);
      sheet.close();
      siteForm({ servers: d.servers, templates: d.templates }, { server_id: srv.id, node: n });
    });
    act("install", () => openModal({
      title: `Установить ноду на ${srv.host}?`,
      submit: "Установить",
      body: `<p class="muted" style="margin:0">Панель поставит docker, положит ноду в
          <code>${esc(srv.node_path)}</code>, пропишет ключ этой панели и запустит контейнер.</p>
        <label class="check"><input type="checkbox" name="tune"><span>Включить BBR и поднять лимиты</span></label>
        <label class="check"><input type="checkbox" name="force"><span>Перезаписать, если нода там уже есть</span></label>`,
      onSubmit: async (v) => {
        const r = await post(`/api/nodes/${n.uuid}/ssh/install`, { tune: !!v.tune, force: !!v.force });
        sheet.close();
        jobConsole(r.job, `Установка ноды · ${n.name}`);
      },
    }));
    act("srv-edit", () => { sheet.close(); sshForm(n, srv); });
    act("ssh-check", (e) => busy(e.currentTarget, async () => {
      try { const r = await post(`/api/nodes/${n.uuid}/ssh/check`); toast(r.info); redraw(false); }
      catch (x) { toast(x.message, true); }
    }));
    act("ssh-drop", () => confirmModal(`Отвязать сервер от ноды ${n.name}?`,
      "Панель забудет доступ и список настроенных CDN. На сервере ничего не изменится.",
      async () => { await api(`/api/nodes/${n.uuid}/ssh`, { method: "DELETE" }); toast("Сервер отвязан"); sheet.close(); redraw(false); },
      "Отвязать"));
    $$("[data-cdn-del]", sheet).forEach((b) => b.addEventListener("click", () => openModal({
      title: `Снять CDN ${b.dataset.name}?`, submit: "Снять", danger: true,
      body: `<p class="muted">Панель удалит конфиг nginx и перечитает его. Инбаунд на ноде останется как есть.</p>
        <label class="check"><input type="checkbox" name="drop_cert"><span>Удалить и сертификат Let's Encrypt</span></label>`,
      onSubmit: async (v) => {
        const r = await api(`/api/cdn/sites/${b.dataset.cdnDel}?drop_cert=${v.drop_cert ? "true" : "false"}`, { method: "DELETE" });
        sheet.close();
        jobConsole(r.job, `Снятие CDN ${b.dataset.name}`);
      },
    })));
  } else {
    act("ssh-add", () => { sheet.close(); sshForm(n, null); });
  }
}

function hostSheet(h) {
  const params = secCard("link", "Параметры", `<dl class="kv">
      ${kvRow("Адрес", `${esc(h.address)}:${h.port}`)}
      ${kvRow("Инбаунд", esc(h.inbound_tag || "—"))}
      ${kvRow("Слой безопасности", esc(({ DEFAULT: "как в инбаунде", TLS: "TLS", NONE: "без TLS" })[h.security_layer] || h.security_layer || "как в инбаунде"))}
      ${kvRow("Path", esc(h.path || "—"))}
      ${kvRow("SNI", esc(h.sni || "—"))}
      ${kvRow("Host", esc(h.host || "—"))}
      ${kvRow("ALPN / Fingerprint", esc([h.alpn, h.fingerprint].filter(Boolean).join(" · ") || "—"))}
      ${kvRow("Подпись", esc(h.server_description || "—"))}
    </dl>
    ${h.allow_insecure ? `<p class="hint" style="margin:12px 0 0">Разрешён недоверенный сертификат.</p>` : ""}`,
    h.disabled ? "off" : "up");

  const sheet = openSheet({
    title: esc(h.remark),
    lead: icon("link"),
    uuid: h.uuid,
    tag: h.disabled ? `<span class="tag off">выключен</span>` : `<span class="tag ok">включён</span>`,
    body: `${params}
      ${secCard("grid", "Действия", tiles(
        tileBtn("reach", "globe", "Из России", "cyan"),
        tileBtn("edit", "edit", "Изменить"),
        h.disabled ? tileBtn("toggle", "power", "Включить", "green")
                   : tileBtn("toggle", "power", "Выключить", "amber"),
        tileBtn("delete", "trash", "Удалить", "red")))}`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  act("edit", () => { sheet.close(); hostForm(h); });
  act("reach", () => reachCheck([`${h.address}:${h.port}`], `Из России · ${h.remark}`));
  act("toggle", (e) => busy(e.currentTarget, async () => {
    try {
      await post(`/api/hosts/${h.uuid}/${h.disabled ? "enable" : "disable"}`);
      toast("Готово"); sheet.close(); redraw(false);
    } catch (x) { toast(x.message, true); }
  }));
  act("delete", () => confirmModal(`Удалить хост ${h.remark}?`, "Он пропадёт из подписок у всех клиентов.",
    async () => { await api(`/api/hosts/${h.uuid}`, { method: "DELETE" }); toast("Хост удалён"); sheet.close(); redraw(false); }));
}

/* итог массовой проверки SSH */
function checkReport(r) {
  const rows = [...r.results].sort((a, b) => a.ok - b.ok);
  openSheet({
    title: `Проверка SSH · ${r.ok} из ${r.total}`,
    lead: icon("check"),
    tag: r.ok === r.total ? `<span class="tag ok">все доступны</span>` : `<span class="tag bad">${r.total - r.ok} без доступа</span>`,
    body: `<div class="mini-list" style="margin:0">${rows.map((x) => `<div class="mini-row ${x.ok ? "" : "bad"}">
        <span><b>${x.ok ? "✓" : "✗"} ${esc(x.name)}</b><span class="sub">${esc(x.info || "")}</span></span>
      </div>`).join("")}</div>`,
  });
}

/* ---------- карточки сервера, CDN-домена и шаблона ---------- */
function serverSheet(srv, d) {
  if (!srv) return;
  const node = (d.nodes || []).find((n) => n.uuid === srv.node_uuid);
  const failed = srv.last_fail && (!srv.last_ok || srv.last_fail > srv.last_ok);
  const sheet = openSheet({
    title: esc(srv.name),
    lead: icon("ssh"),
    uuid: `${srv.username}@${srv.host}:${srv.port}`,
    tag: failed ? `<span class="tag bad">нет доступа</span>`
      : srv.last_ok ? `<span class="tag ok">${ago(srv.last_ok)}</span>` : `<span class="tag warn">не проверялся</span>`,
    body: `${secCard("ssh", "Подключение", `<dl class="kv">
          ${kvRow("Вход", srv.auth === "key" ? "приватный ключ" : "пароль")}
          ${kvRow("Каталог ноды", esc(srv.node_path))}
          ${kvRow("Нода", node ? `${flagOf(node.country)} ${esc(node.name)}` : "<span class='muted'>не привязана</span>")}
          ${srv.note ? kvRow("Заметка", esc(srv.note)) : ""}
        </dl>
        ${srv.last_info ? `<p class="hint" style="margin:12px 0 0">${esc(srv.last_info)}</p>` : ""}`,
        failed ? "down" : srv.last_ok ? "up" : "")}
      ${secCard("grid", "Действия", tiles(
        tileBtn("term", "term", "Терминал", "cyan"),
        tileBtn("logs", "logs", "Лог ноды", "blue"),
        tileBtn("check", "check", "Проверить SSH"),
        tileBtn("update", "download", "Обновить ноду", "green"),
        tileBtn("hy2", "bolt", "Hysteria2", "violet"),
        tileBtn("cdn", "pulse", "Состояние CDN"),
        tileBtn("edit", "edit", "Изменить"),
        tileBtn("delete", "trash", "Удалить", "red")))}`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  act("check", (e) => busy(e.currentTarget, async () => {
    try { const r = await post(`/api/servers/${srv.id}/check`); toast(r.info); sheet.close(); redraw(false); }
    catch (x) { toast(x.message, true); redraw(false); }
  }));
  act("update", () => confirmModal(`Обновить ноду на ${srv.name}?`, "docker compose pull и up -d. Нода перезапустится.",
    async () => { const r = await post(`/api/servers/${srv.id}/update`); sheet.close(); jobConsole(r.job, `Обновление · ${srv.name}`); }, "Обновить"));
  act("hy2", () => { sheet.close(); hy2Form(srv.id, srv.name); });
  act("term", () => { sheet.close(); openTerminal(srv); });
  act("logs", () => { sheet.close(); openLogs(srv); });
  act("cdn", (e) => runJob(e.currentTarget, `/api/servers/${srv.id}/cdn-status`, {}, `Состояние CDN · ${srv.name}`));
  act("edit", () => { sheet.close(); serverForm(srv, d.nodes || []); });
  act("delete", () => confirmModal(`Удалить сервер ${srv.name}?`,
    "Из панели пропадут доступы и список его CDN. На самом сервере ничего не изменится.",
    async () => { await api(`/api/servers/${srv.id}`, { method: "DELETE" }); toast("Сервер удалён"); sheet.close(); redraw(false); }));
}

function siteSheet(site, d) {
  if (!site) return;
  const list = (site.domains || site.domain || "").split(" ").filter(Boolean);
  const sheet = openSheet({
    title: esc(site.domain),
    lead: icon("cloud"),
    uuid: `${site.path} → 127.0.0.1:${site.upstream}`,
    tag: site.is_default_server ? `<span class="tag info">default_server</span>` : "",
    body: `${secCard("cloud", "Конфиг", `<dl class="kv">
          ${kvRow("Домены", list.map(esc).join("<br>"))}
          ${kvRow("Сервер", esc(site.server_name || "—"))}
          ${kvRow("Шаблон", esc(site.template_name || "—"))}
          ${kvRow("Применён", ago(site.ts))}
        </dl>`, "up")}
      ${secCard("grid", "Действия", tiles(
        tileBtn("reach", "globe", "Из России", "cyan"),
        tileBtn("again", "refresh", "Применить заново", "blue"),
        tileBtn("drop", "trash", "Снять CDN", "red")))}
      <p class="hint" style="margin:0">«Применить заново» перезапишет конфиг nginx из шаблона.
        Живой сертификат не перевыпускается.</p>`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  act("reach", () => reachCheck(list.map((x) => `${x}:443`), `Из России · ${site.domain}`));
  act("again", () => {
    sheet.close();
    siteForm(d, { server_id: site.server_id, template_id: site.template_id, domain: list.join(", "),
                  path: site.path, upstream: site.upstream, is_default_server: !!site.is_default_server });
  });
  act("drop", () => openModal({
    title: `Снять CDN ${site.domain}?`, submit: "Снять", danger: true,
    body: `<p class="muted">Панель удалит конфиг nginx и перечитает его. Инбаунд на ноде останется как есть.</p>
      <label class="check"><input type="checkbox" name="drop_cert"><span>Удалить и сертификат Let's Encrypt</span></label>`,
    onSubmit: async (v) => {
      const r = await api(`/api/cdn/sites/${site.id}?drop_cert=${v.drop_cert ? "true" : "false"}`, { method: "DELETE" });
      sheet.close();
      jobConsole(r.job, `Снятие CDN ${site.domain}`);
    },
  }));
}

function templateSheet(t) {
  if (!t) return;
  const sheet = openSheet({
    title: esc(t.name),
    lead: icon("cloud"),
    uuid: t.slug,
    tag: t.builtin ? `<span class="tag info">встроенный</span>` : "",
    body: `${secCard("tool", "По умолчанию", `<dl class="kv">
          ${kvRow("Путь", esc(t.default_path || "/"))}
          ${kvRow("Порт", t.default_port || 10085)}
          ${kvRow("default_server", t.is_default_server ? "да" : "нет")}
        </dl>
        ${t.note ? `<p class="hint" style="margin:12px 0 0">${esc(t.note)}</p>` : ""}`)}
      ${secCard("grid", "Конфиг nginx", `<pre class="console" style="max-height:38vh;border-radius:12px;margin:0">${esc(t.body)}</pre>`)}
      ${secCard("grid", "Действия", tiles(
        tileBtn("edit", "edit", "Изменить"),
        tileBtn("delete", "trash", "Удалить", "red")))}`,
  });
  $(`[data-a="edit"]`, sheet).addEventListener("click", () => { sheet.close(); templateForm(t); });
  $(`[data-a="delete"]`, sheet).addEventListener("click", () =>
    confirmModal(`Удалить шаблон ${t.name}?`,
      t.builtin ? "Встроенный шаблон вернётся при следующем запуске панели." : "Уже настроенные домены это не затронет.",
      async () => { await api(`/api/cdn/templates/${t.id}`, { method: "DELETE" }); toast("Шаблон удалён"); sheet.close(); redraw(false); }));
}

/* ---------- формы задач ---------- */
function hy2Form(id, label, byNode = false) {
  openModal({
    title: `Hysteria2 · ${label}`,
    submit: "Настроить",
    body: `<p class="muted" style="margin:0">Панель выпустит сертификат Let's Encrypt, примонтирует его в контейнер ноды
      (<code>/var/lib/remnawave/configs/xray/ssl/</code>), переведёт образ на <code>latest</code> и перезапустит ноду.</p>
      <label>Домен ноды<input name="domain" required placeholder="node.example.com">
        <span class="hint">A-запись должна смотреть на этот сервер, 80/tcp открыт снаружи</span></label>
      <label>E-mail для Let's Encrypt<input name="email" type="email" placeholder="необязательно"></label>
      <label>Токен Cloudflare API<input name="cf_token" type="password" autocomplete="off" placeholder="необязательно">
        <span class="hint">Нужен, если домен за CDN или 80-й порт снаружи закрыт: тогда сертификат
          выпускается через DNS-01 и адрес домена не проверяется. Права токена — Zone:DNS:Edit.
          Панель его не хранит, только передаёт на сервер</span></label>
      <span class="hint">Без токена панель сначала сверит A-запись домена с IP сервера публичным
        резолвером и остановится, если они разные — чтобы не жечь лимиты Let's Encrypt.</span>`,
    onSubmit: async (v) => {
      const path = byNode ? `/api/nodes/${id}/ssh/hysteria2` : `/api/servers/${id}/hysteria2`;
      const r = await post(path, { domain: v.domain.trim(), email: v.email.trim(),
                                   cf_token: (v.cf_token || "").trim() });
      jobConsole(r.job, `Hysteria2 · ${label}`);
    },
  });
}

function templateForm(existing) {
  const t = existing || { body: "" };
  openModal({
    title: existing ? `Шаблон: ${t.name}` : "Новый шаблон nginx",
    wide: true,
    submit: existing ? "Сохранить" : "Добавить",
    body: `<div class="form-grid">
      <label>Название<input name="name" value="${esc(t.name || "")}" required maxlength="64"></label>
      <label>Идентификатор<input name="slug" value="${esc(t.slug || "")}" ${existing ? "readonly" : ""}
        required pattern="[a-z0-9][a-z0-9-]{1,30}" placeholder="vk-cloud">
        <span class="hint">Латиница и дефис — попадёт в имя файла конфига</span></label>
    </div>
    <div class="form-grid">
      <label>Путь по умолчанию<input name="default_path" value="${esc(t.default_path || "/")}"></label>
      <label>Порт по умолчанию<input name="default_port" type="number" min="1" max="65535" value="${t.default_port || 10085}"></label>
    </div>
    <label class="check"><input type="checkbox" name="is_default_server" ${t.is_default_server ? "checked" : ""}>
      <span>По умолчанию ставить default_server</span></label>
    <label>Заметка<input name="note" value="${esc(t.note || "")}"></label>
    <label>Конфиг nginx<textarea name="body" rows="16" required spellcheck="false">${esc(t.body || "")}</textarea>
      <span class="hint">Плейсхолдеры: <code>{{server_names}}</code>, <code>{{domain}}</code>,
        <code>{{path}}</code>, <code>{{port}}</code>, <code>{{upstream}}</code>, <code>{{default}}</code>,
        <code>{{cert}}</code>, <code>{{key}}</code>. Свой <code>proxy_set_header</code> в location отключит
        заголовки из общего конфига — не задавай их здесь.</span></label>`,
    onSubmit: async (v) => {
      const payload = {
        name: v.name, slug: (v.slug || t.slug || "").toLowerCase(), body: v.body, note: v.note || "",
        default_path: v.default_path || "/", default_port: Number(v.default_port) || 10085,
        is_default_server: !!v.is_default_server,
      };
      if (existing) await api(`/api/cdn/templates/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await post("/api/cdn/templates", payload);
      toast("Шаблон сохранён");
      redraw(false);
    },
  });
}

function siteForm(d, preset = {}) {
  const el = openModal({
    title: "Настроить CDN",
    wide: true,
    submit: "Применить",
    body: `<div class="form-grid">
      <label>Сервер<select name="server_id" required ${preset.node ? "disabled" : ""}>
        ${optList(d.servers.map((s) => [s.id, `${s.name} (${s.host})`]), preset.server_id)}</select>
        ${preset.node ? `<span class="hint">Нода ${esc(preset.node.name)}</span>` : ""}</label>
      <label>Шаблон<select name="template_id" id="tpl-sel" required>${optList(d.templates.map((t) => [t.id, t.name]), preset.template_id)}</select>
        <span class="hint" id="tpl-note"></span></label>
    </div>
    <label>Домены<input name="domain" required placeholder="vk.example.com, vkontakte.example.org">
      <span class="hint">Первый — основной, на него выпишется сертификат. Остальные через запятую попадут
        в server_name и, если не отмечено ниже, в тот же сертификат</span></label>
    <label class="check"><input type="checkbox" name="cert_primary_only" id="f-cert1">
      <span>Сертификат только на основной домен — остальные (техдомен Timeweb) только в server_name</span></label>
    <div class="form-grid">
      <label>Путь<input name="path" id="f-path" required placeholder="/static/getFile/video/segment.ts">
        <span class="hint">Ровно тот, что в инбаунде xhttp</span></label>
      <label>Локальный порт инбаунда<input name="upstream" id="f-port" type="number" min="1" max="65535" value="10085">
        <span class="hint">Инбаунд слушает 127.0.0.1 на этом порту</span></label>
    </div>
    <label class="check"><input type="checkbox" name="is_default_server" id="f-def">
      <span>default_server — ловить запросы без SNI и с чужим SNI</span></label>
    <div class="form-grid">
      <label>E-mail для Let's Encrypt<input name="email" type="email" placeholder="необязательно"></label>
      <label>Токен Cloudflare API<input name="cf_token" type="password" autocomplete="off" placeholder="необязательно">
        <span class="hint">Для домена, который уже за CDN, — единственный рабочий способ: DNS-01</span></label>
    </div>
    <span class="hint">Общий http-конфиг (буферы, реальный IP, keepalive к Xray) панель поставит сама
      в <code>/etc/nginx/conf.d/rd-cdn-common.conf</code>. Для второго CDN просто повтори с другим доменом.</span>`,
    onSubmit: async (v) => {
      const r = await post("/api/cdn/sites", {
        server_id: Number(v.server_id || preset.server_id || 0),
        node_uuid: preset.node ? preset.node.uuid : "",
        template_id: Number(v.template_id),
        domain: v.domain.trim(), path: v.path.trim(), upstream: Number(v.upstream) || 10085,
        email: v.email.trim(), cf_token: (v.cf_token || "").trim(),
        is_default_server: !!v.is_default_server, cert_primary_only: !!v.cert_primary_only,
      });
      jobConsole(r.job, `CDN ${v.domain.trim().split(/[,\s]+/)[0]}`);
    },
  });
  const sel = $("#tpl-sel", el);
  const fill = (fromTemplate) => {
    const t = d.templates.find((x) => String(x.id) === sel.value);
    if (!t) return;
    $("#tpl-note", el).textContent = t.note || "";
    $("#f-cert1", el).checked = t.slug === "timeweb";
    if (!fromTemplate && preset.domain) {            // повторное применение — значения свои
      $("#f-path", el).value = preset.path || "/";
      $("#f-port", el).value = preset.upstream || 10085;
      $("#f-def", el).checked = !!preset.is_default_server;
      return;
    }
    $("#f-path", el).value = t.default_path || "/";
    $("#f-port", el).value = t.default_port || 10085;
    $("#f-def", el).checked = !!t.is_default_server;
  };
  sel.addEventListener("change", () => fill(true));
  fill(false);
}

/* ============================================================
   Пользователи, сквады и инструменты Remnawave
   ============================================================ */

const GB2 = 1024 ** 3;
const STRAT = [["NO_RESET", "не сбрасывать"], ["DAY", "каждый день"],
               ["WEEK", "каждую неделю"], ["MONTH", "1-го числа"],
               ["MONTH_ROLLING", "раз в месяц от создания"]];
let picked = new Set();
let squadsCache = null;

async function getSquads(force) {
  if (!squadsCache || force) squadsCache = await api("/api/squads").catch(() => []);
  return squadsCache;
}

const dateOnly = (v) => (v ? new Date(v).toLocaleDateString("ru-RU") : "—");
const dateTime = (v) => (v ? new Date(v).toLocaleString("ru-RU") : "—");

/* ---------- список пользователей ---------- */
usersBody = (d) => {
  if (!d.items.length) return `<p class="empty">Никого не нашлось. Измени запрос или фильтр.</p>`;
  return `<div class="table-wrap"><table class="rows">
    <thead><tr><th class="grip"></th><th>Пользователь</th><th>Статус</th><th class="num">Трафик</th>
      <th>Истекает</th><th>Онлайн</th><th></th></tr></thead>
    <tbody>${d.items.map((u) => `<tr data-open="${u.uuid || ""}" data-short="${esc(u.short_uuid || "")}" tabindex="0" role="button">
      <td class="grip pick"><input type="checkbox" ${u.uuid ? "" : "disabled"} data-pick="${u.uuid}" ${picked.has(u.uuid) ? "checked" : ""}
        aria-label="Выбрать ${esc(u.username)}"></td>
      <td><b>${esc(u.username)}</b>${u.telegram_id ? `<span class="sub">tg ${esc(u.telegram_id)}</span>` : ""}</td>
      <td>${statusTag(u.status)}</td>
      <td class="num" data-l="Трафик">${fmtBytes(u.used)}<span class="sub">${u.limit ? "из " + fmtBytes(u.limit) : "без лимита"}</span></td>
      <td data-l="Истекает">${dateOnly(u.expire_at)}</td>
      <td class="muted" data-l="Онлайн">${ago(u.online_at)}</td>
      <td class="chev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></td>
    </tr>`).join("")}</tbody></table></div>
    <div class="pager">Найдено ${fmtNum(d.total)} · страница ${d.page} из ${d.pages}
      <button class="btn btn-sm" id="u-prev" ${d.page <= 1 ? "disabled" : ""}>Назад</button>
      <button class="btn btn-sm" id="u-next" ${d.page >= d.pages ? "disabled" : ""}>Дальше</button></div>`;
};

bindUsersBody = () => {
  $("#u-prev")?.addEventListener("click", () => { usersState.page--; reloadUsers(); });
  $("#u-next")?.addEventListener("click", () => { usersState.page++; reloadUsers(); });
  $$("[data-pick]").forEach((cb) => cb.addEventListener("click", (e) => {
    e.stopPropagation();
    cb.checked ? picked.add(cb.dataset.pick) : picked.delete(cb.dataset.pick);
    drawBulkBar();
  }));
  $$("tr[data-open]").forEach((tr) => {
    const go = (e) => { if (!e.target.closest("[data-pick]")) userSheet(tr.dataset.open, tr.dataset.short); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(e); } });
  });
  drawBulkBar();
};

function drawBulkBar() {
  const bar = $("#u-bulk");
  if (!bar) return;
  bar.hidden = picked.size === 0;
  const label = $("#u-bulk-n", bar);
  if (label) label.textContent = picked.size;
}

/* ---------- карточка пользователя ---------- */
async function userSheet(uuid, short) {
  let u, devices = [], squads = [];
  const q = uuid ? "" : `?short=${encodeURIComponent(short || "")}`;
  if (!uuid && !short) return toast("Панель не вернула идентификатор этого пользователя", true);
  try {
    [u, squads] = await Promise.all([api(`/api/users/${uuid || "null"}/full${q}`), getSquads()]);
    uuid = u.uuid || uuid;
    devices = await api(`/api/users/${uuid}/devices`).catch(() => []);
  } catch (e) { return toast(e.message, true); }
  const quotas = await api(`/api/users/${uuid}/quotas`).catch(() => []);

  const names = Object.fromEntries(squads.map((s) => [s.uuid, s.name]));
  const mine = (u.squads || []).map((x) => names[x] || x).join(", ");
  const sheet = openSheet({
    title: u.username,
    tag: statusTag(u.status),
    body: `<dl class="kv">
        ${kvRow("Трафик", `${fmtBytes(u.used)}${u.traffic_limit_gb ? ` из ${u.traffic_limit_gb} ГБ` : " · без лимита"}`)}
        ${kvRow("Сброс трафика", esc((STRAT.find((s) => s[0] === u.traffic_strategy) || [, "—"])[1]))}
        ${kvRow("Истекает", dateOnly(u.expire_at))}
        ${kvRow("Был онлайн", ago(u.online_at))}
        ${kvRow("Сквады", esc(mine || "—"))}
        ${kvRow("Telegram", esc(u.telegram_id || "—"))}
        ${kvRow("E-mail", esc(u.email || "—"))}
        ${kvRow("Тег", esc(u.tag || "—"))}
        ${kvRow("Устройств", `${devices.length}${u.hwid_limit ? ` из ${u.hwid_limit}` : ""}`)}
        ${kvRow("Подписку открывали", ago(u.sub_last_opened))}
      </dl>
      ${u.description ? `<p class="hint" style="margin:0">${esc(u.description)}</p>` : ""}
      ${quotas.length ? secCard("gauge", "Квоты", `<div class="mini-list">${quotas.map((q) => {
          const pct = Math.min(100, Math.round(q.used * 100 / (q.limit || 1)));
          const until = new Date(q.reset).toLocaleDateString("ru-RU");
          const mode = !q.enabled ? " · правило выключено" : q.mode === "dry" ? " · тест" : q.mode === "desc" ? " · только счётчик" : "";
          const line = {
            out: `нет сквада с лимитом — квота не действует`,
            in: `${bytesGb(q.used)} из ${bytesGb(q.limit)} ГБ`,
            exempt: `исключён — без лимита`,
            moved: `лимит исчерпан · на запасном скваде до ${until}`,
          }[q.status];
          const btn = {
            out: `<button class="btn btn-sm btn-primary" data-q="${q.rule_id}" data-qa="grant">Под квоту</button>`,
            in: `<button class="btn btn-sm" data-q="${q.rule_id}" data-qa="exempt">Исключить</button>`,
            exempt: `<button class="btn btn-sm" data-q="${q.rule_id}" data-qa="include">Вернуть под квоту</button>`,
            moved: `<button class="btn btn-sm btn-primary" data-q="${q.rule_id}" data-qa="restore">Вернуть доступ</button>`,
          }[q.status];
          return `<div class="mini-row ${q.status === "moved" ? "bad" : ""}"><span><b>${esc(q.rule)}</b>
            ${q.status === "in" || q.status === "moved" ? `<span class="quota-bar"><i style="width:${Math.max(pct, 1)}%" class="${pct >= 100 ? "full" : pct >= 80 ? "near" : ""}"></i></span>` : ""}
            <span class="sub mono">${line}${mode}</span></span>${btn}</div>`;
        }).join("")}</div>`) : ""}
      ${u.subscription_url ? secCard("link", "Ссылка подписки", `
        <div class="copy-row"><code id="sub-url">${esc(u.subscription_url)}</code>
          <button class="icon-btn" data-a="copy" aria-label="Копировать">${icon("copy")}</button></div>`) : ""}
      ${secCard("grid", "Действия", tiles(
        tileBtn("edit", "edit", "Изменить"),
        u.status === "DISABLED" ? tileBtn("toggle", "power", "Включить", "green")
                                : tileBtn("toggle", "power", "Отключить", "amber"),
        tileBtn("extend", "clock", "Продлить", "green"),
        tileBtn("reset", "reset", "Сброс трафика"),
        tileBtn("revoke", "refresh", "Новая подписка", "blue"),
        tileBtn("delete", "trash", "Удалить", "red")))}
      ${secCard("install", `Устройства · ${devices.length}${u.hwid_limit ? ` из ${u.hwid_limit}` : ""}`,
        devices.length ? `<div class="mini-list">${devices.map((dv) => `<div class="mini-row">
            <span><b>${esc(dv.platform || dv.model || "устройство")}</b>
              <span class="sub">${esc([dv.model, dv.os].filter(Boolean).join(" · "))}</span>
              ${dv.app ? `<span class="sub mono">${esc(dv.app)}</span>` : ""}
              <span class="sub">${ago(dv.updated_at || dv.created_at)}</span></span>
            <span style="display:flex;gap:6px">
              <button class="icon-btn danger" data-ban-dev="${esc(dv.hwid)}" aria-label="Забанить устройство" title="Забанить HWID — прослойка перестанет отдавать ему подписку на любом аккаунте">${icon("shield")}</button>
              <button class="icon-btn danger" data-hwid="${esc(dv.hwid)}" aria-label="Сбросить устройство">${icon("trash")}</button></span>
          </div>`).join("")}</div>
          <div class="tile-grid" style="margin-top:10px">${tileBtn("hwid-all", "trash", "Сбросить все", "red")}</div>`
          : `<p class="hint" style="margin:0">Пока ни одного.</p>`)}`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  act("copy", async (e) => {
    try { await navigator.clipboard.writeText(u.subscription_url); toast("Ссылка скопирована"); }
    catch { toast("Скопируй вручную", true); }
  });
  act("edit", () => { sheet.close(); userForm(u, squads); });
  act("toggle", (e) => busy(e.currentTarget, async () => {
    try {
      await post(`/api/users/${uuid}/${u.status === "DISABLED" ? "enable" : "disable"}`);
      toast("Готово"); sheet.close(); reloadUsers();
    } catch (x) { toast(x.message, true); }
  }));
  const QA_TEXT = {
    grant: ["Поставить под квоту?", "Пользователю добавится сквад с лимитом из правила. С этого момента его трафик на нодах правила считается.", "Поставить"],
    exempt: ["Исключить из квоты?", "Доступ к нодам правила останется, но без лимита: правило перестанет его считать. Если он сейчас ограничен — доступ вернётся сразу.", "Исключить"],
    include: ["Вернуть под квоту?", "Правило снова начнёт считать трафик пользователя на нодах с лимитом.", "Вернуть"],
    restore: ["Вернуть доступ досрочно?", "Сквад с лимитом вернётся сейчас. Если пользователь всё ещё выше лимита, при следующей проверке правило ограничит его снова — чтобы этого не было, лучше «Исключить».", "Вернуть"],
  };
  $$("[data-q]", sheet).forEach((b) => b.addEventListener("click", () => {
    const [title, text, word] = QA_TEXT[b.dataset.qa];
    confirmModal(title, text, async () => {
      const r = await post(`/api/users/${uuid}/quotas/${b.dataset.q}`, { action: b.dataset.qa });
      toast(r.message); sheet.close(); reloadUsers();
    }, word);
  }));
  act("extend", () => bindDays(openModal({
    title: `Продлить ${u.username}`, submit: "Продлить",
    body: `<p class="muted" style="margin:0">Сейчас действует до ${dateOnly(u.expire_at)}.
        Дни добавятся к этой дате, а не к сегодняшней.</p>
      <div class="chip-row">${[7, 30, 90, 365].map((dd) => `<button type="button" class="pill-btn" data-days="${dd}">${dd} дн.</button>`).join("")}</div>
      <label>Дней<input name="days" type="number" min="1" max="3650" value="30" required></label>`,
    onSubmit: async (v) => {
      await post(`/api/users/${uuid}/extend`, { days: Number(v.days) });
      toast("Продлено"); sheet.close(); reloadUsers();
    },
  })));
  act("reset", () => confirmModal("Сбросить трафик?", `Счётчик ${u.username} обнулится.`,
    async () => { await post(`/api/users/${uuid}/reset-traffic`); toast("Трафик сброшен"); sheet.close(); reloadUsers(); },
    "Сбросить"));
  act("revoke", () => confirmModal("Перевыпустить подписку?",
    "Старая ссылка перестанет работать — клиенту нужно будет выдать новую.",
    async () => { await post(`/api/users/${uuid}/revoke`); toast("Подписка перевыпущена"); sheet.close(); reloadUsers(); },
    "Перевыпустить"));
  act("delete", () => confirmModal(`Удалить ${u.username}?`, "Пользователь и его подписка исчезнут.",
    async () => { await api(`/api/users/${uuid}`, { method: "DELETE" }); toast("Удалён"); sheet.close(); reloadUsers(); }));
  act("hwid-all", () => confirmModal("Сбросить все устройства?",
    "Клиент сможет заново привязать лимит устройств.",
    async () => { await api(`/api/users/${uuid}/devices`, { method: "DELETE" }); toast("Устройства сброшены"); sheet.close(); },
    "Сбросить"));
  $$("[data-ban-dev]", sheet).forEach((b) => b.addEventListener("click", () => confirmModal("Забанить устройство?",
    `HWID ${b.dataset.banDev}. Прослойка перестанет отдавать ему подписку — на этом и любом другом аккаунте. `
    + "Уже скачанные конфиги работают, пока не нажать «Новая подписка».",
    async () => { await post("/api/bans", { kind: "hwid", value: b.dataset.banDev, note: `устройство ${u.username}` }); toast("Устройство в бане"); },
    "Забанить")));
  $$("[data-hwid]", sheet).forEach((b) => b.addEventListener("click", () => busy(b, async () => {
    try {
      await api(`/api/users/${uuid}/devices?hwid=${encodeURIComponent(b.dataset.hwid)}`, { method: "DELETE" });
      toast("Устройство сброшено"); b.closest(".mini-row").remove();
    } catch (x) { toast(x.message, true); }
  })));
}

/* быстрые кнопки «7 / 30 / 90 / 365 дней» */
function bindDays(el) {
  $$("[data-days]", el).forEach((b) => b.addEventListener("click", () => {
    $("[name=days]", el).value = b.dataset.days;
    $$("[data-days]", el).forEach((x) => x.classList.toggle("on", x === b));
  }));
  return el;
}

/* ---------- форма пользователя ---------- */
async function userForm(existing, squads) {
  squads = squads || await getSquads();
  const quotaSquads = Object.fromEntries((await api("/api/quotas").catch(() => []))
    .map((r) => [r.full_squad, r.name]));
  const u = existing || { traffic_strategy: "NO_RESET", traffic_limit_gb: 0, squads: [] };
  const days = existing?.expire_at
    ? Math.max(0, Math.round((new Date(existing.expire_at) - Date.now()) / 86400000)) : 30;
  openModal({
    title: existing ? `Пользователь: ${u.username}` : "Новый пользователь",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<div class="form-grid">
      <label>Имя<input name="username" value="${esc(u.username || "")}" required minlength="3" maxlength="36"
        pattern="[A-Za-z0-9_-]{3,36}"><span class="hint">3–36 символов: латиница, цифры, дефис, подчёркивание</span></label>
      <label>Действует ещё, дней<input name="expire_days" type="number" min="0" max="3650" value="${days}">
        <span class="hint">Отсчёт от сегодня${existing ? ` · сейчас до ${dateOnly(u.expire_at)}` : ""}</span></label>
      <label>Лимит трафика, ГБ<input name="traffic_limit_gb" type="number" min="0" step="1"
        value="${u.traffic_limit_gb || 0}"><span class="hint">0 — без лимита</span></label>
      <label>Сброс трафика<select name="traffic_strategy">${optList(STRAT, u.traffic_strategy)}</select></label>
      <label>Telegram ID<input name="telegram_id" value="${esc(u.telegram_id || "")}" inputmode="numeric"></label>
      <label>E-mail<input name="email" type="email" value="${esc(u.email || "")}"></label>
      <label>Тег<input name="tag" value="${esc(u.tag || "")}" maxlength="16" placeholder="VIP"></label>
      <label>Лимит устройств<input name="hwid_limit" type="number" min="0" max="100"
        value="${u.hwid_limit ?? ""}" placeholder="как в настройках панели"></label>
    </div>
    <label>Сквады<span class="hint">Определяют, какие инбаунды получит клиент</span></label>
    ${squads.length ? `<div class="checks">${squads.map((s) => `<label class="check">
        <input type="checkbox" name="squads" value="${esc(s.uuid)}" ${(u.squads || []).includes(s.uuid) ? "checked" : ""}>
        <span>${esc(s.name)}${s.users != null ? ` · ${s.users}` : ""}${quotaSquads[s.uuid] ? ` <em class="sq-q">квота «${esc(quotaSquads[s.uuid])}»</em>` : ""}</span></label>`).join("")}</div>`
      : `<span class="hint">Сквадов нет — создай их в разделе «Сквады».</span>`}
    <label>Описание<input name="description" value="${esc(u.description || "")}"></label>`,
    onSubmit: async (v, form) => {
      const payload = {
        username: v.username.trim(),
        expire_days: v.expire_days === "" ? null : Number(v.expire_days),
        traffic_limit_gb: Number(v.traffic_limit_gb) || 0,
        traffic_strategy: v.traffic_strategy,
        squads: $$("input[name=squads]:checked", form).map((i) => i.value),
        telegram_id: (v.telegram_id || "").trim(), email: (v.email || "").trim(),
        tag: (v.tag || "").trim(), description: (v.description || "").trim(),
        hwid_limit: v.hwid_limit === "" ? null : Number(v.hwid_limit),
      };
      if (existing) await api(`/api/users/${existing.uuid}`, { method: "PUT", body: JSON.stringify(payload) });
      else {
        const r = await post("/api/users", payload);
        if (r.subscription_url) toast("Создан. Ссылка подписки — в карточке");
      }
      toast(existing ? "Сохранено" : "Пользователь создан");
      reloadUsers();
    },
  });
}

/* ---------- массовые действия ---------- */
async function bulkForm() {
  const squads = await getSquads();
  openModal({
    title: `Массовое действие · ${picked.size}`,
    wide: true,
    submit: "Выполнить",
    danger: true,
    body: `<label>Действие<select name="action" id="b-act">${optList([
        ["reset-traffic", "Сбросить трафик"], ["revoke-subscription", "Перевыпустить подписки"],
        ["extend", "Продлить срок"], ["update", "Сменить стратегию сброса"], ["update-squads", "Заменить сквады"],
        ["delete", "Удалить"]])}</select></label>
      <div id="b-extra"></div>
      <span class="hint">Действие применится к ${picked.size} выбранным пользователям и не отменяется.</span>`,
    onSubmit: async (v, form) => {
      const payload = { action: v.action, uuids: [...picked] };
      if (v.action === "extend") payload.expire_days = Number(v.expire_days) || 0;
      if (v.action === "update" && v.traffic_strategy) payload.traffic_strategy = v.traffic_strategy;
      if (v.action === "update-squads") payload.squads = $$("input[name=squads]:checked", form).map((i) => i.value);
      await post("/api/users-bulk", payload);
      picked.clear();
      toast("Готово");
      reloadUsers();
    },
  });
  const extra = $("#b-extra");
  const sel = $("#b-act");
  const fill = () => {
    if (sel.value === "extend") {
      extra.innerHTML = `<label>Продлить на, дней<input name="expire_days" type="number" min="1" max="3650" value="30" required>
        <span class="hint">Каждому добавится к его текущей дате окончания</span></label>`;
    } else if (sel.value === "update") {
      extra.innerHTML = `<label>Сброс трафика<select name="traffic_strategy">${optList(STRAT)}</select></label>`;
    } else if (sel.value === "update-squads") {
      extra.innerHTML = squads.length
        ? `<div class="checks">${squads.map((s) => `<label class="check">
            <input type="checkbox" name="squads" value="${esc(s.uuid)}"><span>${esc(s.name)}</span></label>`).join("")}</div>`
        : `<span class="hint">Сквадов нет.</span>`;
    } else extra.innerHTML = "";
  };
  sel.addEventListener("change", fill);
  fill();
}

/* ---------- разделы ---------- */
Object.assign(VIEWS, {
  users: {
    title: "Пользователи", sub: "Нажми на строку — карточка, галочка слева — массовые действия",
    load: () => api(`/api/users?${new URLSearchParams(usersState)}`),
    actions: (d) => `<span class="muted" style="font-size:13px">Всего ${fmtNum(d.all)}</span>
      <button class="btn btn-sm btn-primary" id="u-add">Добавить</button>${refreshBtn}`,
    draw: (d) => `<div class="panel" data-a>
      <div class="toolbar">
        <div class="search">${icon("search")}<input id="u-q" type="search"
          placeholder="Имя, Telegram ID или short UUID" value="${esc(usersState.q)}" aria-label="Поиск"></div>
        <div class="seg" id="u-seg">${[["", "Все", d.all], ["ACTIVE", "Активные"], ["DISABLED", "Отключённые"],
            ["LIMITED", "Лимит"], ["EXPIRED", "Истёкшие"]]
          .map(([k, l, c]) => `<button data-s="${k}" class="${k === usersState.status ? "on" : ""}">${l}<b>${fmtNum(c ?? d.counts[k] ?? 0)}</b></button>`).join("")}</div>
      </div>
      <div class="bulk-bar" id="u-bulk" hidden>
        <span>Выбрано <b id="u-bulk-n">0</b></span>
        <button class="btn btn-sm" id="u-bulk-go">Действие</button>
        <button class="btn btn-sm btn-ghost" id="u-bulk-clear">Снять</button>
      </div>
      <div id="u-body">${usersBody(d)}</div></div>`,
    bind: (d, root) => {
      bindRefresh(root);
      $("#u-add").addEventListener("click", () => userForm(null));
      $("#u-bulk-go").addEventListener("click", () => bulkForm());
      $("#u-bulk-clear").addEventListener("click", () => { picked.clear(); reloadUsers(); });
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

  squads: {
    title: "Сквады", sub: "Наборы инбаундов, которые выдаются пользователям",
    load: async () => {
      const [squads, profiles] = await Promise.all([api("/api/squads"), getProfiles(true)]);
      squadsCache = squads;
      return { squads, profiles };
    },
    actions: () => `<button class="btn btn-sm btn-primary" id="sq-add">Новый сквад</button>${refreshBtn}`,
    draw: (d) => `<div class="panel" data-a>${d.squads.length ? `<div class="table-wrap"><table class="rows">
      <thead><tr><th>Сквад</th><th class="num">Пользователей</th><th class="num">Инбаундов</th><th></th></tr></thead>
      <tbody>${d.squads.map((s) => `<tr data-sq="${s.uuid}" tabindex="0" role="button">
        <td><b>${esc(s.name)}</b></td>
        <td class="num" data-l="Пользователей">${s.users ?? "—"}</td>
        <td class="num" data-l="Инбаундов">${s.inbounds_count ?? s.inbounds.length}</td>
        <td class="chev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></td>
      </tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Сквадов нет. Сквад — это набор инбаундов: пользователь получает то, что в нём.</p>`}</div>`,
    bind: (d, root) => {
      bindRefresh(root);
      $("#sq-add").addEventListener("click", () => squadForm(null, d.profiles));
      $$("tr[data-sq]", root).forEach((tr) => {
        const go = () => squadSheet(d.squads.find((s) => s.uuid === tr.dataset.sq), d.profiles);
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
      });
    },
  },

  tools: {
    title: "Инструменты", sub: "Служебные утилиты панели",
    load: () => api("/api/metrics").catch(() => ({})),
    actions: () => refreshBtn,
    draw: (m) => {
      const bw = m.bandwidth && !m.bandwidth.error ? m.bandwidth : null;
      const pick = (k) => bw?.[k]?.current ?? bw?.[k]?.total ?? null;
      return `<div class="panel" data-a><h2>Трафик панели</h2>
        ${bw ? `<dl class="kv">
          ${kvRow("Сегодня", esc(String(pick("bandwidthLastTwoDays") ?? pick("today") ?? "—")))}
          ${kvRow("За неделю", esc(String(pick("bandwidthLastSevenDays") ?? pick("week") ?? "—")))}
          ${kvRow("За месяц", esc(String(pick("bandwidthCalendarMonth") ?? pick("month") ?? "—")))}
          ${kvRow("Устройств всего", esc(String(m.devices?.totalDevices ?? m.devices?.total ?? "—")))}
        </dl>` : `<p class="hint" style="margin:0">Панель не отдала статистику трафика.</p>`}
      </div>
      <div class="panel" data-a><h2>Доступность из России</h2>
        <p class="hint" style="margin:-4px 0 12px">TCP-проверка с точек check-host.net в российских сетях. Можно несколько адресов через запятую.</p>
        <div class="copy-row" style="padding:6px 6px 6px 12px">
          <input id="t-reach" placeholder="node.example.com:443, 1.2.3.4:8443" style="border:0;background:none;padding:6px 0">
          <button class="btn btn-sm btn-primary" id="t-reach-go">Проверить</button></div>
      </div>
      <div class="panel" data-a><h2>Ключи Reality (x25519)</h2>
        <p class="hint" style="margin:-4px 0 12px">Пара ключей для инбаунда с Reality: приватный идёт на ноду, публичный — клиентам.</p>
        <button class="btn btn-sm btn-primary" id="t-x25519">Сгенерировать</button>
        <div id="t-x25519-out"></div>
      </div>
`;
    },
    bind: (m, root) => {
      bindRefresh(root);
      $("#t-reach-go").addEventListener("click", () => {
        const t = $("#t-reach").value.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
        if (!t.length) return toast("Впиши адрес", true);
        reachCheck(t.slice(0, 20));
      });
      $("#t-x25519").addEventListener("click", (e) => busy(e.currentTarget, async () => {
        try {
          const r = await api("/api/tools/x25519");
          $("#t-x25519-out").innerHTML = `<dl class="kv" style="margin-top:14px">
            ${kvRow("Приватный", `<code>${esc(r.privateKey || r.private_key || "")}</code>`)}
            ${kvRow("Публичный", `<code>${esc(r.publicKey || r.public_key || "")}</code>`)}
            ${r.password ? kvRow("Password", `<code>${esc(r.password)}</code>`) : ""}</dl>`;
        } catch (x) { toast(x.message, true); }
      }));
    },
  },
});

/* ---------- сквады: карточка и форма ---------- */
function squadSheet(sq, profiles) {
  if (!sq) return;
  const all = profiles.flatMap((p) => p.inbounds.map((i) => [i.uuid, `${p.name} · ${inboundLabel(i)}`]));
  const mine = sq.inbounds.map((u) => (all.find((x) => x[0] === u) || [, u])[1]);
  const sheet = openSheet({
    title: sq.name,
    body: `<dl class="kv">
        ${kvRow("Пользователей", sq.users ?? "—")}
        ${kvRow("Инбаундов", sq.inbounds_count ?? sq.inbounds.length)}
      </dl>
      ${mine.length ? `<section class="sheet-sec"><h4>Инбаунды</h4>
        <div class="mini-list">${mine.map((n) => `<div class="mini-row"><span>${esc(n)}</span></div>`).join("")}</div>
      </section>` : ""}
      ${secCard("grid", "Действия", tiles(
        tileBtn("edit", "edit", "Изменить"),
        tileBtn("add-all", "users", "Добавить всех", "green"),
        tileBtn("remove-all", "unlink", "Убрать всех", "amber"),
        tileBtn("delete", "trash", "Удалить", "red")))}`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  act("edit", () => { sheet.close(); squadForm(sq, profiles); });
  act("add-all", () => confirmModal("Добавить всех пользователей?",
    `Каждый пользователь панели получит инбаунды сквада ${sq.name}.`,
    async () => { await post(`/api/squads/${sq.uuid}/add-all`); toast("Готово"); sheet.close(); redraw(false); },
    "Добавить"));
  act("remove-all", () => confirmModal("Убрать всех пользователей?",
    `Сквад ${sq.name} отвяжется от всех пользователей.`,
    async () => { await post(`/api/squads/${sq.uuid}/remove-all`); toast("Готово"); sheet.close(); redraw(false); },
    "Убрать"));
  act("delete", () => confirmModal(`Удалить сквад ${sq.name}?`,
    "Пользователи останутся, но потеряют эти инбаунды.",
    async () => { await api(`/api/squads/${sq.uuid}`, { method: "DELETE" }); toast("Сквад удалён"); sheet.close(); redraw(false); }));
}

function squadForm(existing, profiles) {
  const sq = existing || { name: "", inbounds: [] };
  openModal({
    title: existing ? `Сквад: ${sq.name}` : "Новый сквад",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<label>Название<input name="name" value="${esc(sq.name)}" required minlength="2" maxlength="32"></label>
      <label>Инбаунды<span class="hint">Что получит пользователь, попавший в этот сквад</span></label>
      ${profiles.map((p) => `<details class="more" open><summary>${esc(p.name)}</summary>
        <div class="checks">${p.inbounds.map((i) => `<label class="check">
          <input type="checkbox" name="inbounds" value="${esc(i.uuid)}" ${sq.inbounds.includes(i.uuid) ? "checked" : ""}>
          <span>${esc(inboundLabel(i))}</span></label>`).join("")}</div></details>`).join("")}`,
    onSubmit: async (v, form) => {
      const payload = { name: v.name.trim(), inbounds: $$("input[name=inbounds]:checked", form).map((i) => i.value) };
      if (existing) await api(`/api/squads/${existing.uuid}`, { method: "PUT", body: JSON.stringify(payload) });
      else await post("/api/squads", payload);
      toast(existing ? "Сквад сохранён" : "Сквад создан");
      redraw(false);
    },
  });
}

/* ============================================================
   Живые инструменты: терминал, лог, проверка из России, сертификаты
   ============================================================ */

const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

/* xterm.js грузим только при первом открытии терминала */
let xtermReady = null;
function loadXterm() {
  if (xtermReady) return xtermReady;
  const add = (tag, attrs) => new Promise((ok, bad) => {
    const el = Object.assign(document.createElement(tag), attrs);
    el.onload = ok; el.onerror = () => bad(new Error("Не удалось загрузить xterm.js"));
    document.head.appendChild(el);
  });
  // лежит в самой панели: jsDelivr из России открывается не всегда
  const base = "/static/vendor";
  xtermReady = Promise.all([
    add("link", { rel: "stylesheet", href: `${base}/xterm.css` }),
    add("script", { src: `${base}/xterm.js` }),
  ]).then(() => add("script", { src: `${base}/addon-fit.js` }))
    .catch((e) => { xtermReady = null; throw e; });
  return xtermReady;
}

/* клавиши, которых нет на телефонной клавиатуре */
const EXTRA_KEYS = [
  ["Esc", "\x1b"], ["Tab", "\t"], ["^C", "\x03"], ["^D", "\x04"], ["^L", "\x0c"],
  ["↑", "\x1b[A"], ["↓", "\x1b[B"], ["←", "\x1b[D"], ["→", "\x1b[C"],
  ["|", "|"], ["/", "/"], ["-", "-"], ["~", "~"],
];

async function openTerminal(srv) {
  const el = document.createElement("div");
  el.className = "modal-wrap term-wrap";
  el.innerHTML = `<div class="modal term-modal">
    <header class="sheet-head"><span class="row-tile">${icon("term")}</span>
      <span class="sheet-title"><h3>${esc(srv.name)}</h3><span class="sheet-id">${esc(srv.username)}@${esc(srv.host)}</span></span>
      <span class="tag info" id="t-state">подключение</span>
      <button type="button" class="icon-btn" data-close aria-label="Закрыть">${icon("plus").replace("<svg", '<svg style="transform:rotate(45deg)"')}</button></header>
    <div class="term-box" id="t-box"></div>
    <div class="term-keys">${EXTRA_KEYS.map(([l, v], i) => `<button type="button" data-k="${i}">${esc(l)}</button>`).join("")}</div>
  </div>`;
  document.body.appendChild(el);
  el.dataset.locked = "1";
  lockScroll(true);
  requestAnimationFrame(() => el.classList.add("show"));

  let ws = null, term = null, onResize = null;
  const state = (text, cls) => { const t = $("#t-state", el); t.textContent = text; t.className = `tag ${cls}`; };
  const close = () => { try { ws?.close(); } catch { /* уже закрыт */ } window.removeEventListener("resize", onResize); term?.dispose(); closeModal(el); };
  $("[data-close]", el).addEventListener("click", close);

  try { await loadXterm(); }
  catch (e) { state("ошибка", "bad"); $("#t-box", el).innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }

  term = new window.Terminal({
    cursorBlink: true, fontSize: window.innerWidth < 720 ? 12 : 13, scrollback: 5000,
    fontFamily: '"JetBrains Mono", ui-monospace, Menlo, monospace',
    theme: { background: "#07090c", foreground: "#e6edf3", cursor: "#2bd4a8", selectionBackground: "#2bd4a855",
             green: "#2bd4a8", brightGreen: "#5ff0c8", blue: "#4c8dff", cyan: "#39c5cf", red: "#f85149", yellow: "#f0b73e" },
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("#t-box", el));
  fit.fit();

  ws = new WebSocket(wsUrl(`/ws/terminal/${srv.id}?c=${term.cols}&r=${term.rows}`));
  const send = (m) => { if (ws.readyState === 1) ws.send(JSON.stringify(m)); };
  ws.onopen = () => { state("на связи", "ok"); term.focus(); };
  ws.onmessage = (e) => term.write(e.data);
  ws.onclose = (e) => state(e.code === 4401 ? "нет доступа" : "закрыт", e.code === 4401 ? "bad" : "off");
  term.onData((d) => send({ t: "i", d }));
  term.onResize(({ cols, rows }) => send({ t: "r", c: cols, r: rows }));
  onResize = () => { try { fit.fit(); } catch { /* окно закрыто */ } };
  window.addEventListener("resize", onResize);
  $$("[data-k]", el).forEach((b) => b.addEventListener("click", () => {
    send({ t: "i", d: EXTRA_KEYS[Number(b.dataset.k)][1] });
    term.focus();
  }));
}

function openLogs(srv) {
  const el = document.createElement("div");
  el.className = "modal-wrap";
  el.innerHTML = `<div class="modal wide logs-modal">
    <header class="sheet-head"><span class="row-tile">${icon("logs")}</span>
      <span class="sheet-title"><h3>Лог ноды · ${esc(srv.name)}</h3><span class="sheet-id">docker compose logs -f</span></span>
      <span class="tag info" id="l-state">подключение</span>
      <button type="button" class="icon-btn" data-close aria-label="Закрыть">${icon("plus").replace("<svg", '<svg style="transform:rotate(45deg)"')}</button></header>
    <div class="logs-bar">
      <div class="search">${icon("search")}<input id="l-q" type="search" placeholder="Фильтр: error, warn, имя пользователя…"></div>
      <button type="button" class="tool-btn" id="l-pause" aria-label="Пауза">${icon("clock")}</button>
      <button type="button" class="tool-btn" id="l-clear" aria-label="Очистить">${icon("trash")}</button>
    </div>
    <pre class="console logs-out" id="l-out"></pre>
  </div>`;
  document.body.appendChild(el);
  el.dataset.locked = "1";
  lockScroll(true);
  requestAnimationFrame(() => el.classList.add("show"));

  const out = $("#l-out", el);
  const lines = [];
  let q = "", paused = false;
  const tone = (l) => /error|fatal|panic|failed/i.test(l) ? "e" : /warn/i.test(l) ? "w" : "";
  const render = () => {
    const shown = q ? lines.filter((l) => l.toLowerCase().includes(q)) : lines;
    out.innerHTML = shown.slice(-1500).map((l) => `<span class="ln ${tone(l)}">${esc(l)}</span>`).join("");
    if (!paused) out.scrollTop = out.scrollHeight;
  };
  const ws = new WebSocket(wsUrl(`/ws/logs/${srv.id}?tail=300`));
  const state = (t, c) => { const s = $("#l-state", el); s.textContent = t; s.className = `tag ${c}`; };
  ws.onopen = () => state("поток", "ok");
  ws.onclose = (e) => state(e.code === 4401 ? "нет доступа" : "закрыт", e.code === 4401 ? "bad" : "off");
  let pending = false;
  ws.onmessage = (e) => {
    for (const l of String(e.data).split("\n")) if (l) lines.push(l);
    if (lines.length > 5000) lines.splice(0, lines.length - 5000);
    if (!paused && !pending) { pending = true; requestAnimationFrame(() => { pending = false; render(); }); }
  };
  $("#l-q", el).addEventListener("input", (e) => { q = e.target.value.trim().toLowerCase(); render(); });
  $("#l-pause", el).addEventListener("click", (e) => {
    paused = !paused; e.currentTarget.classList.toggle("on", paused); state(paused ? "пауза" : "поток", paused ? "warn" : "ok");
    if (!paused) render();
  });
  $("#l-clear", el).addEventListener("click", () => { lines.length = 0; render(); });
  $("[data-close]", el).addEventListener("click", () => { try { ws.close(); } catch { /* */ } closeModal(el); });
}

/* ---------- доступность из России ---------- */
async function reachCheck(targets, title = "Доступность из России") {
  const el = openSheet({
    title: esc(title), lead: icon("globe"),
    uuid: "TCP через check-host.net, точки в РФ",
    tag: `<span class="tag info" id="r-state">проверяю</span>`,
    body: `<div id="r-out"><p class="hint" style="margin:0">Запускаю проверку ${targets.length} адрес(ов)… обычно 10–20 секунд.</p>
      <div class="skeleton-bars">${"<i></i>".repeat(Math.min(targets.length, 4))}</div></div>`,
  });
  try {
    const r = await post("/api/reach", { targets });
    const bad = r.results.filter((x) => x.ok < x.total).length;
    const st = $("#r-state", el);
    st.textContent = bad ? `${bad} с проблемами` : "всё доступно";
    st.className = `tag ${bad ? "bad" : "ok"}`;
    $("#r-out", el).innerHTML = r.results.map((x) => {
      const tone = x.ok === x.total ? "up" : x.ok === 0 ? "down" : "";
      return secCard("globe", x.target, `<div class="reach-grid">${x.points.map((p) => `
          <span class="reach ${p.state}" title="${esc(p.error || "")}">
            <b>${esc(p.city)}</b><i>${p.state === "ok" ? `${p.ms} мс` : p.state === "wait" ? "нет ответа" : esc(p.error || "ошибка")}</i>
          </span>`).join("")}</div>`, tone, `${x.ok} из ${x.total} точек видят`);
    }).join("") + `<p class="hint" style="margin:0">Проверяется, открывается ли TCP-порт из российских сетей —
      так видна блокировка по IP или порту. Блокировку протокола (DPI) этот тест не покажет.</p>`;
  } catch (e) {
    const st = $("#r-state", el); st.textContent = "ошибка"; st.className = "tag bad";
    $("#r-out", el).innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}

/* ---------- раздел «Сертификаты» ---------- */
Object.assign(VIEWS, {
  certs: {
    title: "Сертификаты", sub: "", bare: true,
    load: () => api("/api/certs"),
    actions: () => "",
    draw: (rows) => {
      const all = rows.flatMap((r) => r.certs.map((c) => ({ ...c, server: r.server, server_id: r.server_id })))
        .sort((a, b) => a.days - b.days);
      const soon = all.filter((c) => c.days < 14 && c.days >= 0).length;
      const dead = all.filter((c) => c.days < 0).length;
      const errs = rows.filter((r) => r.error);
      return `<div class="grid kpis">
          ${kpiCard("cyan", "shield", "Сертификатов", `<span data-count="${all.length}">${all.length}</span>`, `на ${rows.length} серверах`)}
          ${kpiCard(soon ? "amber" : "green", "clock", "Истекают < 14 дн.", `<span data-count="${soon}">${soon}</span>`, soon ? "продли заранее" : "запас есть")}
          ${kpiCard(dead ? "red" : "muted", "unlink", "Истекли", `<span data-count="${dead}">${dead}</span>`, dead ? "клиенты получают ошибку TLS" : "—")}
          ${kpiCard(errs.length ? "red" : "muted", "ssh", "Нет доступа", `<span data-count="${errs.length}">${errs.length}</span>`, errs.length ? "серверы не ответили" : "все опрошены")}
        </div>
        <div class="panel list-head" data-a>
          <div class="list-title"><span class="row-tile big-tile">${icon("shield")}</span><h2>Сертификаты</h2></div>
          <div class="tool-row"><button class="tool-btn cyan" data-refresh aria-label="Опросить заново">${icon("refresh")}</button></div>
        </div>
        ${all.length ? `<div class="cards">${all.map((c) => {
          const tone = c.days < 0 ? "down" : c.days < 14 ? "warn" : "up";
          return `<article class="card-row ${tone === "warn" ? "" : tone}" data-cert="${esc(c.name)}" data-sid="${c.server_id}" tabindex="0" role="button">
            <header><span class="row-tile">${icon("shield")}</span><b class="name">${esc(c.name)}</b>
              <span class="tag ${tone === "up" ? "ok" : tone === "warn" ? "warn" : "bad"}">${c.days < 0 ? "истёк" : `${c.days} дн.`}</span></header>
            <p class="addr mono">${c.domains.map(esc).join(" · ")}</p>
            <div class="row-meta"><span class="pill accent">${esc(c.server)}</span>
              <span class="pill">до ${new Date(c.expires * 1000).toLocaleDateString("ru-RU")}</span></div>
          </article>`;
        }).join("")}</div>` : `<div class="panel"><p class="empty">Сертификатов не найдено. Они появятся после настройки Hysteria2 или CDN.</p></div>`}
        ${errs.length ? `<div class="panel"><h2>Не удалось опросить</h2><div class="mini-list">${errs.map((e) =>
          `<div class="mini-row bad"><span><b>${esc(e.server)}</b><span class="sub">${esc(e.error)}</span></span></div>`).join("")}</div></div>` : ""}`;
    },
    bind: (rows, root) => {
      $("[data-refresh]", root)?.addEventListener("click", (e) => busy(e.currentTarget, () => redraw(false)));
      $$("[data-cert]", root).forEach((card) => card.addEventListener("click", () => {
        const name = card.dataset.cert, sid = card.dataset.sid;
        openModal({
          title: `Продлить ${name}?`, submit: "Продлить",
          body: `<p class="muted" style="margin:0">certbot сам решит, пора ли: продление идёт, когда до конца меньше 30 дней.</p>
            <label class="check"><input type="checkbox" name="force"><span>Принудительно — даже если срок ещё большой</span></label>
            <span class="hint">Принудительное продление расходует лимит Let's Encrypt: не больше 5 раз в неделю на один набор доменов.</span>`,
          onSubmit: async (v) => {
            const r = await post(`/api/servers/${sid}/cert-renew`, { name, force: !!v.force });
            jobConsole(r.job, `Сертификат ${name}`);
          },
        });
      }));
    },
  },
});

/* ============================================================
   Квоты трафика на ноды
   ============================================================ */
const PERIODS = [["month", "календарный месяц (сброс 1-го)"], ["days", "каждые N дней"]];
const bytesGb = (b) => (b / GB).toFixed(b / GB < 10 ? 1 : 0).replace(/\.0$/, "");

const QUOTA_MODES = [
  ["dry", "тест — только считать, ничего не менять"],
  ["desc", "только счётчик — писать цифры клиентам, никого не переводить"],
  ["active", "боевой — счётчик и перевод при превышении"],
];

let quotaEvery = 10;

function quotaIntervalForm(iv) {
  const el = openModal({
    title: "Как часто проверять квоты", submit: "Сохранить",
    body: `<div class="chip-row">${[1, 5, 10, 30, 60].map((m) =>
        `<button type="button" class="pill-btn ${m === iv.minutes ? "on" : ""}" data-min="${m}">${m} мин</button>`).join("")}</div>
      <label>Интервал, минут<input name="minutes" type="number" min="1" max="1440" value="${iv.minutes}" required>
        <span class="hint">От 1 минуты до суток. Одно на все правила</span></label>
      <p class="hint" style="margin:0">Remnawave записывает трафик пользователей каждые 15 секунд, так что частая проверка
        имеет смысл: чем короче интервал, тем меньше пользователь успеет потратить сверх лимита.
        Цена — запросы к панели: на каждый прогон один на список пользователей и по одному на каждую ноду правила.</p>
      ${iv.next_run ? `<p class="hint" style="margin:0">Следующая проверка ${new Date(iv.next_run * 1000).toLocaleTimeString("ru-RU")}.
        После сохранения — сразу.</p>` : ""}`,
    onSubmit: async (v) => {
      const r = await api("/api/quotas/interval", { method: "PUT", body: JSON.stringify({ minutes: Number(v.minutes) }) });
      toast(`Квоты проверяются каждые ${r.minutes} мин.`); redraw(false);
    },
  });
  $$("[data-min]", el).forEach((b) => b.addEventListener("click", () => {
    $("[name=minutes]", el).value = b.dataset.min;
    $$("[data-min]", el).forEach((x) => x.classList.toggle("on", x === b));
  }));
}

function quotaTone(r) {
  if (!r.enabled) return ["off", "выключено"];
  return r.mode === "active" ? ["ok", "работает"] : r.mode === "desc" ? ["info", "счётчик"] : ["warn", "тест"];
}

Object.assign(VIEWS, {
  quotas: {
    title: "Квоты", sub: "", bare: true,
    load: async () => {
      const [rules, nodes, squads, iv] = await Promise.all([
        api("/api/quotas"), api("/api/nodes").catch(() => []), getSquads(true),
        api("/api/quotas/interval").catch(() => ({ minutes: 10 }))]);
      quotaEvery = iv.minutes;
      return { rules, nodes, squads, interval: iv };
    },
    actions: () => "",
    draw: (d) => {
      const nodeName = Object.fromEntries(d.nodes.map((n) => [n.uuid, n]));
      const tot = (k) => d.rules.reduce((a, r) => a + ((r.last_result || {})[k] || 0), 0);
      const moved = d.rules.reduce((a, r) => a + ((r.last_result || {}).moved || []).length, 0);
      return `<div class="grid kpis">
          ${kpiCard("cyan", "gauge", "Правил", `<span data-count="${d.rules.length}">${d.rules.length}</span>`,
            `${d.rules.filter((r) => r.enabled && r.mode === "active").length} работают`)}
          ${kpiCard("blue", "users", "Под квотой", `<span data-count="${tot("in_scope")}">${tot("in_scope")}</span>`, "пользователей")}
          ${kpiCard(tot("near") ? "amber" : "muted", "clock", "Близко к лимиту", `<span data-count="${tot("near")}">${tot("near")}</span>`, "80% и больше")}
          ${kpiCard(tot("over") ? "red" : "muted", "unlink", "Исчерпали", `<span data-count="${tot("over")}">${tot("over")}</span>`, "лимит исчерпан")}
        </div>
        <div class="panel list-head" data-a>
          <div class="list-title"><span class="row-tile big-tile">${icon("gauge")}</span><h2>Квоты</h2></div>
          <div class="tool-row">
            <button class="tool-btn interval-btn" id="q-interval" aria-label="Как часто проверять">${icon("clock")}<b>${d.interval.minutes}м</b></button>
            <button class="tool-btn cyan" data-refresh aria-label="Обновить">${icon("refresh")}</button>
            <button class="tool-btn green" id="q-add" aria-label="Новое правило">${icon("plus")}</button>
          </div>
        </div>
        ${d.rules.length ? `<div class="cards">${d.rules.map((r) => {
          const [tone, label] = quotaTone(r);
          const res = r.last_result || {};
          return `<article class="card-row ${r.enabled && r.mode === "active" ? "up" : ""}" data-open="${r.id}" tabindex="0" role="button">
            <header><span class="row-tile">${icon("gauge")}</span><b class="name">${esc(r.name)}</b>
              <span class="tag ${tone}">${label}</span></header>
            <div class="meter"><b class="big">${r.limit_gb} ГБ</b>
              <span class="lim">${r.period === "month" ? "в месяц" : `за ${r.period_days} дн.`}</span></div>
            <div class="row-meta">${r.nodes.map((u) => nodeName[u]
              ? `<span class="pill">${flagOf(nodeName[u].country)} ${esc(nodeName[u].name)}</span>`
              : `<span class="pill">нода удалена</span>`).join("")}</div>
            <footer class="live">
              <span>${icon("users")}<b>${res.in_scope ?? "—"}</b></span>
              <span>${icon("clock")}<b>${res.near ?? "—"}</b></span>
              <span>${icon("unlink")}<b>${res.over ?? "—"}</b></span>
              <span style="margin-left:auto">сброс ${new Date(r.reset).toLocaleDateString("ru-RU")}</span>
            </footer>
          </article>`;
        }).join("")}</div>`
        : `<div class="panel"><p class="empty">Правил нет. Правило ограничивает трафик пользователя на выбранных нодах:
            исчерпал — переводится в сквад без этих нод до начала нового периода.</p></div>`}`;
    },
    bind: (d, root) => {
      $("[data-refresh]", root)?.addEventListener("click", (e) => busy(e.currentTarget, () => redraw(false)));
      $("#q-interval", root)?.addEventListener("click", () => quotaIntervalForm(d.interval));
      $("#q-add").addEventListener("click", () => quotaForm(null, d));
      bindCards(root, (id) => quotaSheet(d.rules.find((r) => String(r.id) === id), d));
    },
  },
});

function quotaForm(existing, d) {
  const r = existing || { name: "", nodes: [], limit_gb: 300, period: "month", period_days: 30,
    anchor: new Date().toISOString().slice(0, 10), write_desc: 1, mode: "dry", enabled: 1,
    desc_ok: "{used} из {limit} ГБ", desc_over: "лимит исчерпан · вернётся {reset}" };
  const el = openModal({
    title: existing ? `Квота: ${r.name}` : "Новая квота",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<div class="form-grid">
        <label>Название<input name="name" value="${esc(r.name)}" required maxlength="60" placeholder="Например: Обход или Премиум-ноды"></label>
        <label>Лимит на пользователя, ГБ<input name="limit_gb" type="number" min="1" step="1" value="${r.limit_gb}" required>
          <span class="hint">Суммарно по всем выбранным нодам</span></label>
        <label>Период<select name="period" id="q-period">${optList(PERIODS, r.period)}</select></label>
        <label id="q-days-l">Длина, дней<input name="period_days" type="number" min="1" max="366" value="${r.period_days}"></label>
        <label id="q-anchor-l">Отсчёт с<input name="anchor" type="date" value="${esc(r.anchor || "")}"></label>
      </div>
      <label>Ноды с ограничением<span class="hint">Трафик пользователя на них складывается и сравнивается с лимитом</span></label>
      <div class="checks">${d.nodes.map((n) => `<label class="check"><input type="checkbox" name="nodes" value="${n.uuid}"
        ${r.nodes.includes(n.uuid) ? "checked" : ""}><span>${flagOf(n.country)} ${esc(n.name)}</span></label>`).join("")}</div>
      <div class="form-grid">
        <label>Сквад с лимитом<select name="full_squad" required><option value="">— выбери —</option>
          ${optList(d.squads.map((s) => [s.uuid, s.name]), r.full_squad)}</select>
          <span class="hint">Сквад с инбаундами нод, на которые действует лимит. Его участники под квотой; при исчерпании он у них снимается</span></label>
        <label>Запасной сквад<select name="fallback_squad" required><option value="">— выбери —</option>
          ${optList(d.squads.map((s) => [s.uuid, s.name]), r.fallback_squad)}</select>
          <span class="hint">Выдаётся при исчерпании вместо сквада с лимитом. В нём не должно быть нод с лимитом. Если он у пользователей уже есть — просто останется</span></label>
      </div>
      <details class="more" ${r.write_desc ? "open" : ""}><summary>Показ клиенту в подписке</summary>
        <label class="check"><input type="checkbox" name="write_desc" ${r.write_desc ? "checked" : ""}>
          <span>Писать остаток в описание пользователя</span></label>
        <div class="form-grid">
          <label>Пока лимит есть<input name="desc_ok" value="${esc(r.desc_ok)}"></label>
          <label>Когда исчерпан<input name="desc_over" value="${esc(r.desc_over)}"></label>
        </div>
        <span class="hint">Подстановки: <code>{used}</code> <code>{limit}</code> <code>{left}</code> <code>{percent}</code>
          <code>{reset}</code>. Описание пользователя <b>перезаписывается целиком</b> — если бот продаж или вы храните
          там заметки, они пропадут. Клиенту текст показывается через <code>{{DESCRIPTION}}</code> в названии хоста.</span>
      </details>
      <label>Режим<select name="mode">${optList(QUOTA_MODES, r.mode)}</select>
        <span class="hint">Начни с теста: после прогона увидишь, кого бы перевели, и сверишь цифры с Remnawave</span></label>`,
    onSubmit: async (v, form) => {
      const payload = {
        name: v.name.trim(), limit_gb: Number(v.limit_gb),
        nodes: $$("input[name=nodes]:checked", form).map((i) => i.value),
        period: v.period, period_days: Number(v.period_days) || 30, anchor: v.anchor || "",
        full_squad: v.full_squad, fallback_squad: v.fallback_squad,
        write_desc: !!v.write_desc, desc_ok: v.desc_ok, desc_over: v.desc_over,
        mode: v.mode, enabled: existing ? !!existing.enabled : true,
      };
      if (!payload.nodes.length) throw new Error("Отметь хотя бы одну ноду");
      if (existing) await api(`/api/quotas/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await post("/api/quotas", payload);
      toast(existing ? "Правило сохранено" : "Правило создано — запусти проверку из карточки");
      redraw(false);
    },
  });
  const sync = () => {
    const days = $("#q-period", el).value === "days";
    $("#q-days-l", el).hidden = !days;
    $("#q-anchor-l", el).hidden = !days;
  };
  $("#q-period", el).addEventListener("change", sync);
  sync();
}

async function quotaSheet(r, d) {
  if (!r) return;
  let users = [];
  try { users = await api(`/api/quotas/${r.id}/users`); } catch { /* правило ещё не прогонялось */ }
  const [tone, label] = quotaTone(r);
  const res = r.last_result || {};
  const limit = r.limit_gb * GB;
  const list = users.slice(0, 40);
  const sheet = openSheet({
    title: esc(r.name), lead: icon("gauge"),
    uuid: `${r.limit_gb} ГБ ${r.period === "month" ? "в месяц" : `за ${r.period_days} дн.`} · сброс ${new Date(r.reset).toLocaleDateString("ru-RU")}`,
    tag: `<span class="tag ${tone}">${label}</span>`,
    body: `${r.mode === "dry" && r.write_desc ? `<div class="notice warn">${icon("clock")}<span>
        Правило в тесте — описания пользователей не пишутся, поэтому <code>{{DESCRIPTION}}</code> в подписке пустой.
        Чтобы клиенты видели цифры, переключи режим на «только счётчик» или «боевой».</span></div>` : ""}
      ${secCard("pulse", "Последний прогон", r.last_run ? `
        <div class="chip-grid">
          ${statChip("users", `под квотой ${res.in_scope ?? 0}`, "blue")}
          ${statChip("clock", `близко к лимиту ${res.near ?? 0}`, res.near ? "violet" : "")}
          ${statChip("unlink", `исчерпали ${res.over ?? 0}`, res.over ? "cyan" : "")}
        </div>
        ${res.dry && (res.would_move || []).length ? `<p class="hint" style="margin:12px 0 0">В боевом режиме были бы
          переведены: <b>${res.would_move.map(esc).join(", ")}</b></p>` : ""}
        ${(res.errors || []).length ? `<p class="row-err">${res.errors.slice(0, 3).map(esc).join("<br>")}</p>` : ""}
        <p class="hint" style="margin:10px 0 0">${ago(r.last_run)} · автоматически каждые ${quotaEvery} мин.</p>`
        : `<p class="hint" style="margin:0">Ещё не запускалось. Нажми «Прогнать сейчас».</p>`,
        r.mode === "active" ? "up" : "")}
      ${secCard("grid", "Действия", tiles(
        tileBtn("run", "refresh", "Прогнать сейчас", "cyan"),
        tileBtn("mode", "pulse", "Режим", r.mode === "active" ? "green" : r.mode === "desc" ? "blue" : "amber"),
        tileBtn("explain", "search", "Проверить пользователя", "violet"),
        tileBtn("edit", "edit", "Изменить"),
        tileBtn("restore", "reset", "Вернуть всех", "blue"),
        tileBtn("toggle", "power", r.enabled ? "Выключить" : "Включить"),
        tileBtn("delete", "trash", "Удалить", "red")))}
      ${list.length ? secCard("users", `Пользователи · ${users.length}`, `<div class="mini-list">${list.map((u) => `
          <div class="mini-row ${u.moved ? "bad" : ""}">
            <span><b>${esc(u.username)}</b>
              <span class="quota-bar"><i style="width:${Math.max(u.percent, 1)}%" class="${u.percent >= 100 ? "full" : u.percent >= 80 ? "near" : ""}"></i></span>
              <span class="sub mono">${bytesGb(u.used)} из ${r.limit_gb} ГБ${u.moved ? " · на запасном" : ""}</span></span>
          </div>`).join("")}</div>`) : ""}
      ${secCard("link", "Как показать клиенту", `
        <p class="hint" style="margin:0 0 10px">Remnawave подставляет в название хоста данные конкретного пользователя.
          Впиши в название хоста на ноде с лимитом — и каждый клиент увидит свои цифры:</p>
        <div class="copy-row"><code>🚀 ${esc(r.name)} · {{DESCRIPTION}}</code>
          <button class="icon-btn" data-a="copy-tpl" aria-label="Копировать">${icon("copy")}</button></div>
        <p class="hint" style="margin:10px 0 0">Когда лимит исчерпан, хосты нод с лимитом пропадут из подписки вместе со сквадом.
          Чтобы клиент видел и это, добавь такую же подстановку в хост, который есть в обоих сквадах, например
          <code>🇳🇱 Нидерланды · ${esc(r.name)} {{DESCRIPTION}}</code>. Клиент увидит изменения при следующем обновлении подписки.</p>
        <div class="sec-divider"></div>
        <p class="hint" style="margin:0 0 10px"><b>Баннер в Happ.</b> Заголовок <code>announce</code> в подписке — Happ показывает
          его плашкой над списком серверов. Тоже с подстановкой, у каждого клиента свои цифры. Остальные заголовки подписки не трогаются.</p>
        ${tiles(tileBtn("announce-on", "bell", "Включить баннер", "green"), tileBtn("announce-off", "unlink", "Убрать баннер"))}`)}`,
  });
  const act = (n, fn) => $(`[data-a="${n}"]`, sheet)?.addEventListener("click", fn);
  const save = async (patch) => {
    const payload = { name: r.name, nodes: r.nodes, limit_gb: r.limit_gb, period: r.period, period_days: r.period_days,
      anchor: r.anchor, full_squad: r.full_squad, fallback_squad: r.fallback_squad, write_desc: !!r.write_desc,
      desc_ok: r.desc_ok, desc_over: r.desc_over, mode: r.mode, enabled: !!r.enabled, ...patch };
    await api(`/api/quotas/${r.id}`, { method: "PUT", body: JSON.stringify(payload) });
  };
  act("announce-on", () => openModal({
    title: "Баннер в Happ", submit: "Включить",
    body: `<label>Текст баннера<input name="text" value="📊 ${esc(r.name)}: {{DESCRIPTION}}" maxlength="200">
        <span class="hint">Подстановки Remnawave работают: {{DESCRIPTION}}, {{DAYS_LEFT}}, {{TRAFFIC_USED}}…</span></label>`,
    onSubmit: async (v) => { await post("/api/quotas/announce", { text: v.text }); toast("Баннер включён — клиенты увидят при обновлении подписки"); },
  }));
  act("announce-off", () => confirmModal("Убрать баннер?", "Заголовок announce будет удалён из настроек подписки.",
    async () => { await post("/api/quotas/announce", { remove: true }); toast("Баннер убран"); }, "Убрать"));
  act("copy-tpl", async () => {
    try { await navigator.clipboard.writeText(`🚀 ${r.name} · {{DESCRIPTION}}`); toast("Скопировано"); }
    catch { toast("Скопируй вручную", true); }
  });
  act("run", (e) => busy(e.currentTarget, async () => {
    try {
      const x = await post(`/api/quotas/${r.id}/run`);
      toast(r.mode === "dry" ? `Тест: перевёл бы ${x.would_move.length}, под квотой ${x.in_scope}`
          : r.mode === "desc" ? `Счётчик обновлён у ${x.desc_updated}, под квотой ${x.in_scope}`
          : `Готово: переведено ${x.moved.length}, возвращено ${x.restored.length}, описаний ${x.desc_updated}`);
      sheet.close(); redraw(false);
    } catch (err) { toast(err.message, true); }
  }));
  act("mode", () => openModal({
    title: `Режим · ${r.name}`, submit: "Сохранить",
    body: `<div class="radio-list">${QUOTA_MODES.map(([k, l]) => `<label class="check">
        <input type="radio" name="mode" value="${k}" ${r.mode === k ? "checked" : ""}><span>${esc(l)}</span></label>`).join("")}</div>
      ${r.write_desc ? `<p class="hint" style="margin:0">В режимах «только счётчик» и «боевой» описания пользователей
        из «сквада с лимитом» перезаписываются строкой со счётчиком. Ручные заметки в описании пропадут.</p>` : ""}`,
    onSubmit: async (v) => {
      await save({ mode: v.mode });
      toast({ dry: "Правило в тесте", desc: "Пишу счётчик, никого не перевожу", active: "Правило работает" }[v.mode]);
      sheet.close(); redraw(false);
    },
  }));
  act("explain", () => {
    const el = openModal({
      title: "Проверить пользователя", submit: "Проверить",
      body: `<label>Имя пользователя<input name="username" required placeholder="user_6557282694_b236a6" autocomplete="off"></label>
        <div id="ex-out"></div>`,
      onSubmit: async (v) => {
        const x = await api(`/api/quotas/${r.id}/explain?username=${encodeURIComponent(v.username.trim())}`);
        $("#ex-out", el).innerHTML = !x.found ? `<p class="row-err">${esc(x.reasons[0])}</p>` : `
          <dl class="kv" style="margin-top:6px">
            ${kvRow("Под правилом", x.in_scope ? "да" : "нет")}
            ${kvRow("Натратил на нодах правила", `${bytesGb(x.used)} из ${bytesGb(x.limit)} ГБ`)}
            ${kvRow("Должно быть в описании", `<code>${esc(x.expected)}</code>`)}
            ${kvRow("Сейчас в Remnawave", x.actual ? `<code>${esc(x.actual)}</code>` : "<span class='muted'>пусто</span>")}
          </dl>
          ${x.ok ? `<p class="hint" style="margin:10px 0 0;color:var(--mint)">✓ Всё сходится — клиент увидит это в подписке после её обновления.</p>`
                 : `<div class="notice warn" style="margin-top:10px">${icon("clock")}<span>${x.reasons.map(esc).join("<br>")}</span></div>`}`;
        return "keep";
      },
    });
  });
  act("edit", () => { sheet.close(); quotaForm(r, d); });
  act("toggle", (e) => busy(e.currentTarget, async () => {
    await save({ enabled: !r.enabled }); toast(r.enabled ? "Выключено" : "Включено"); sheet.close(); redraw(false);
  }));
  act("restore", () => confirmModal("Вернуть доступ всем сейчас?",
    "Все, кого правило перевело, получат сквад с лимитом обратно, не дожидаясь нового периода. При следующей проверке превысившие будут переведены снова.",
    async () => { const x = await post(`/api/quotas/${r.id}/restore`); toast(`Возвращено: ${x.restored.length}`); sheet.close(); redraw(false); },
    "Вернуть"));
  act("delete", () => confirmModal(`Удалить квоту «${r.name}»?`,
    "Всем переведённым правило вернёт сквад с лимитом, потом удалится.",
    async () => { const x = await api(`/api/quotas/${r.id}`, { method: "DELETE" }); toast(`Удалено, возвращено: ${x.restored.length}`); sheet.close(); redraw(false); }));
}

/* ============================================================
   Автоматизации: условие → действия, с откатом
   ============================================================ */
const AUTO_ACTIONS = {
  squad_move: "Переместить в другой сквад",
  squad_add: "Добавить сквад",
  squad_remove: "Убрать сквад",
  sub_message: "Сообщение в подписке",
  sub_hide: "Скрыть хосты в подписке",
  extend_days: "Продлить подписку",
};
const EVENT_ACTIONS = ["extend_days", "squad_add", "squad_remove"];

function autoTrigger(t, m) {
  if (t.type === "sub_request") return `обновил подписку, в User-Agent есть «${esc(t.ua)}» <span class="muted">(один раз)</span>`;
  if (t.type === "quota") {
    const r = m.rules.find((x) => x.id === t.rule_id);
    return `квота «${esc(r ? r.name : "удалена")}» ≥ ${t.percent}%`;
  }
  const s = t.when === "expired" ? `подписка истекла${t.days ? ` (до ${t.days} дн. назад)` : ""}` : `истекает через ≤ ${t.days} дн.`;
  return s;
}

function autoAction(a, m) {
  const sq = (u) => esc((m.squads.find((x) => x.uuid === u) || { name: "удалён" }).name);
  if (a.type === "squad_move") return `сквад ${sq(a.from)} → ${sq(a.to)}${a.drop_nodes?.length ? " · разрыв соединений" : ""}`;
  if (a.type === "squad_add") return `+ сквад ${sq(a.squad)}`;
  if (a.type === "squad_remove") return `− сквад ${sq(a.squad)}${a.drop_nodes?.length ? " · разрыв соединений" : ""}`;
  if (a.type === "sub_message") return `сообщение «${esc(a.text.length > 40 ? a.text.slice(0, 40) + "…" : a.text)}»`;
  if (a.type === "sub_hide") return `скрыть ${a.hosts.length} хост(а)`;
  if (a.type === "extend_days") return `+${a.days} дн. к подписке`;
  return esc(a.type);
}

function autoActionRow(a, m) {
  const opts = (sel) => m.squads.map((s) => `<option value="${s.uuid}" ${s.uuid === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  const drop = (a.drop_nodes || []);
  const dropBox = `<details class="more"><summary>Разорвать соединения на нодах (${drop.length})</summary><div class="checks" style="padding-bottom:10px">
    ${m.nodes.map((n) => `<label class="check"><input type="checkbox" data-f="drop" value="${n.uuid}" ${drop.includes(n.uuid) ? "checked" : ""}><span>${flagOf(n.country)} ${esc(n.name)}</span></label>`).join("")}
    </div><span class="hint">Иначе клиент докачивает по уже открытому туннелю</span></details>`;
  let body = "";
  if (a.type === "squad_move") body = `<div class="form-grid"><label>Из сквада<select data-f="from">${opts(a.from)}</select></label>
      <label>В сквад<select data-f="to">${opts(a.to)}</select></label></div>${dropBox}`;
  else if (a.type === "squad_add") body = `<label>Сквад<select data-f="squad">${opts(a.squad)}</select></label>`;
  else if (a.type === "squad_remove") body = `<label>Сквад<select data-f="squad">${opts(a.squad)}</select></label>${dropBox}`;
  else if (a.type === "sub_message") body = `<label>Текст<textarea data-f="text" rows="2" maxlength="300">${esc(a.text || "")}</textarea>
      <span class="hint">Встаёт на место <code>{{RD_MESSAGE}}</code> в объявлении Remnawave, а если его там нет — в начало плашки Happ.
      Можно <code>{{RD_QUOTA_LEFT}}</code>, <code>{{RD_QUOTA_RESET}}</code> и остальные. Только через прослойку</span></label>`;
  else if (a.type === "extend_days") body = `<label>Дней<input data-f="days" type="number" min="1" max="3650" value="${a.days || 7}" style="max-width:160px">
      <span class="hint">К текущей дате окончания; если подписка уже истекла — от сегодня, и она снова станет активной</span></label>`;
  else if (a.type === "sub_hide") body = `<div class="checks">${m.hosts.map((h) => `<label class="check"><input type="checkbox" data-f="host" value="${esc(h)}"
      ${(a.hosts || []).includes(h) ? "checked" : ""}><span>${esc(h)}</span></label>`).join("")}</div>
      <span class="hint">Хост пропадёт из подписки, но доступ не закроется — для этого сквады. Работает для ссылок и Xray JSON</span>`;
  return `<div class="auto-act" data-type="${a.type}"><header><b>${AUTO_ACTIONS[a.type]}</b>
    <button type="button" class="icon-btn" data-del aria-label="Убрать действие">${icon("trash")}</button></header>${body}</div>`;
}

function readActionRow(el) {
  const f = (k) => $(`[data-f="${k}"]`, el);
  const t = el.dataset.type;
  const drop = $$('[data-f="drop"]:checked', el).map((x) => x.value);
  if (t === "squad_move") return { type: t, from: f("from").value, to: f("to").value, drop_nodes: drop };
  if (t === "squad_add") return { type: t, squad: f("squad").value };
  if (t === "squad_remove") return { type: t, squad: f("squad").value, drop_nodes: drop };
  if (t === "sub_message") return { type: t, text: f("text").value };
  if (t === "sub_hide") return { type: t, hosts: $$('[data-f="host"]:checked', el).map((x) => x.value) };
  if (t === "extend_days") return { type: t, days: Number(f("days").value) };
  return { type: t };
}

function autoForm(existing, m) {
  const a = existing || { name: "", enabled: true, mode: "dry", trigger: { type: "quota", rule_id: m.rules[0]?.id, percent: 100, squad: "" }, actions: [] };
  const t = a.trigger;
  const el = openModal({
    title: existing ? `Автоматизация: ${a.name}` : "Новая автоматизация",
    wide: true,
    submit: existing ? "Сохранить" : "Создать",
    body: `<label>Название<input name="name" value="${esc(a.name)}" required maxlength="60" placeholder="Например: Обход исчерпан → База"></label>
      <label>Условие<select name="ttype" id="a-ttype">
        <option value="quota" ${t.type === "quota" ? "selected" : ""}>Порог квоты</option>
        <option value="expiry" ${t.type === "expiry" ? "selected" : ""}>Срок подписки</option>
        <option value="sub_request" ${t.type === "sub_request" ? "selected" : ""}>Обновил подписку (User-Agent) — разово</option></select></label>
      <div id="a-ua"><label>User-Agent содержит<input name="ua" value="${esc(t.ua || "")}" placeholder="ward">
        <span class="hint">Несколько слов — через запятую, регистр не важен. Срабатывает при обновлении подписки через прослойку,
          <b>один раз на пользователя</b> и без отката. User-Agent задаёт клиент — это промо-механика, а не защита</span></label></div>
      <div class="form-grid" id="a-quota">
        <label>Правило квоты<select name="rule_id">${m.rules.map((r) => `<option value="${r.id}" ${r.id === t.rule_id ? "selected" : ""}>${esc(r.name)} · ${Math.round(r.limit_bytes / 1024 ** 3)} ГБ</option>`).join("")}</select>
          <span class="hint">Счёт ведёт движок квот. Чтобы не было двойного перевода, поставь правило в «только счётчик»</span></label>
        <label>Израсходовано, %<input name="percent" type="number" min="1" max="1000" value="${t.percent ?? 100}"></label>
      </div>
      <div class="form-grid" id="a-expiry">
        <label>Когда<select name="when"><option value="before" ${t.when !== "expired" ? "selected" : ""}>истекает через ≤ N дней</option>
          <option value="expired" ${t.when === "expired" ? "selected" : ""}>уже истекла (не больше N дней назад, 0 — сколько угодно)</option></select></label>
        <label>Дней<input name="days" type="number" min="0" max="365" value="${t.days ?? 3}"></label>
      </div>
      <label>Только для пользователей со сквадом<select name="squad"><option value="">— любые —</option>
        ${m.squads.map((s) => `<option value="${s.uuid}" ${s.uuid === t.squad ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>
      <label>Действия<span class="hint">Пока условие выполняется — действуют. Перестало (новый период, продлил подписку), автоматизацию выключили или удалили — откатываются.
        Если сквады за это время поменяли вручную, откат их не трогает</span></label>
      <div id="a-acts">${a.actions.map((x) => autoActionRow(x, m)).join("")}</div>
      <label>Добавить действие<select id="a-add"><option value="">—</option>${Object.entries(AUTO_ACTIONS).map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></label>
      <div class="checks">
        <label class="check"><input type="radio" name="mode" value="dry" ${a.mode !== "active" ? "checked" : ""}><span>Тест — только показать, кого затронет</span></label>
        <label class="check"><input type="radio" name="mode" value="active" ${a.mode === "active" ? "checked" : ""}><span>Боевой — применяется сразу после сохранения</span></label>
        <label class="check"><input type="checkbox" name="enabled" ${a.enabled ? "checked" : ""}><span>Включена</span></label>
      </div>`,
    onSubmit: async (v, form) => {
      const actions = $$(".auto-act", form).map(readActionRow);
      if (!actions.length) throw new Error("Добавь хотя бы одно действие");
      const trigger = v.ttype === "quota" ? { type: "quota", rule_id: Number(v.rule_id), percent: Number(v.percent), squad: v.squad }
        : v.ttype === "sub_request" ? { type: "sub_request", ua: v.ua, squad: v.squad }
        : { type: "expiry", when: v.when, days: Number(v.days), squad: v.squad };
      const payload = { name: v.name.trim(), enabled: !!v.enabled, mode: v.mode, trigger, actions };
      const r = existing ? await api(`/api/automations/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) })
        : await post("/api/automations", payload);
      const res = r.result || {};
      toast(payload.mode === "active" && payload.enabled
        ? `Сохранено: применено ${res.applied?.length ?? 0}, откат ${res.reverted?.length ?? 0}`
        : `Сохранено. Под условие попадает ${res.matched ?? 0}`);
      redraw(false);
    },
  });
  const sync = () => {
    const tt = $("#a-ttype", el).value, ev = tt === "sub_request";
    $("#a-quota", el).hidden = tt !== "quota"; $("#a-expiry", el).hidden = tt !== "expiry"; $("#a-ua", el).hidden = !ev;
    // разовому событию — только разовые действия; продление — только событию
    $$("#a-add option[value]", el).forEach((o) => { if (o.value) o.hidden = ev ? !EVENT_ACTIONS.includes(o.value) : o.value === "extend_days"; });
  };
  $("#a-ttype", el).addEventListener("change", sync); sync();
  $("#a-add", el).addEventListener("change", (e) => {
    if (!e.target.value) return;
    const s0 = m.squads[0]?.uuid;
    $("#a-acts", el).insertAdjacentHTML("beforeend", autoActionRow({ type: e.target.value, from: s0, to: m.squads[1]?.uuid, squad: s0, hosts: [] }, m));
    e.target.value = "";
  });
  $("#a-acts", el).addEventListener("click", (e) => { const b = e.target.closest("[data-del]"); if (b) b.closest(".auto-act").remove(); });
}

async function autoLog(a) {
  const d = await api(`/api/automations/${a.id}/log`);
  const EV = { apply: ["ok", "применено"], revert: ["info", "откат"], forget: ["warn", "откат пропущен"], error: ["bad", "ошибка"],
    would: ["warn", "тест: сработала бы"] };
  const ev = a.trigger.type === "sub_request";
  openSheet({
    title: esc(a.name), subtitle: "", lead: icon("bolt"),
    body: `<h3 style="margin:0 0 8px">${ev ? "Уже получили" : "Сейчас действует"} (${d.held.length})</h3>
      ${d.held.length ? `<p class="muted">${d.held.slice(0, 300).map((h) => esc(h.username)).join(", ")}</p>` : `<p class="muted">Ни на кого.</p>`}
      <h3 style="margin:16px 0 8px">Журнал</h3>
      ${d.log.length ? `<div class="table-wrap"><table><tbody>${d.log.map((l) => `<tr><td class="muted" style="white-space:nowrap">${new Date(l.ts * 1000).toLocaleString("ru-RU")}</td>
        <td>${esc(l.username)}</td><td><span class="tag ${EV[l.event]?.[0] || "off"}">${EV[l.event]?.[1] || esc(l.event)}</span></td>
        <td class="muted">${esc(l.detail || "")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Пусто.</p>`}`,
  });
}

Object.assign(VIEWS, {
  automations: {
    title: "Автоматизации", sub: "Условие → действия. Когда условие перестаёт выполняться, действия откатываются", refresh: 60,
    load: async () => {
      const [list, meta] = await Promise.all([api("/api/automations"), api("/api/automations/meta")]);
      return { list, meta };
    },
    actions: () => `<button class="btn btn-sm btn-primary" id="a-new">${icon("plus")}Новая</button>`,
    draw: ({ list, meta }) => list.length ? `<div class="cards">${list.map((a) => {
      const tag = !a.enabled ? ["off", "выключена"] : a.mode === "active" ? ["ok", "работает"] : ["warn", "тест"];
      const r = a.last_result || {};
      return `<article class="card-row ${a.enabled && a.mode === "active" ? "up" : ""}">
        <header><span class="row-tile">${icon("bolt")}</span><b class="name">${esc(a.name)}</b><span class="tag ${tag[0]}">${tag[1]}</span></header>
        <p style="margin:6px 0"><span class="muted">если</span> ${autoTrigger(a.trigger, meta)}${a.trigger.squad ? ` <span class="muted">· со сквадом ${esc((meta.squads.find((s) => s.uuid === a.trigger.squad) || {}).name || "?")}</span>` : ""}</p>
        <p style="margin:6px 0"><span class="muted">то</span> ${a.actions.map((x) => autoAction(x, meta)).join(" · ")}</p>
        <footer class="live">
          ${a.event ? `<span title="Уже получили">${icon("users")}<b>${a.fired}</b> получили</span>
            ${a.mode !== "active" ? `<span class="muted">в тесте сработала бы у ${a.would}</span>` : ""}`
          : `<span title="Сейчас действует">${icon("users")}<b>${a.held}</b></span>
          <span title="Под условием">${icon("gauge")}<b>${r.matched ?? "—"}</b></span>
          ${r.errors?.length ? `<span class="tag bad">ошибок ${r.errors.length}</span>` : ""}
          <span style="margin-left:auto" class="muted">${a.last_run ? `прогон ${ago(a.last_run)}` : "ещё не запускалась"}</span>`}
        </footer>
        <div class="form-actions" style="margin-top:10px">
          ${a.event ? "" : `<button class="btn btn-sm" data-a="preview" data-id="${a.id}">Кого затронет</button>`}
          <button class="btn btn-sm" data-a="log" data-id="${a.id}">Журнал</button>
          <button class="btn btn-sm" data-a="edit" data-id="${a.id}">Изменить</button>
          <button class="btn btn-sm btn-ghost btn-danger" data-a="del" data-id="${a.id}">Удалить</button>
        </div></article>`;
    }).join("")}</div>` : `<div class="panel" data-a><p class="empty">Автоматизаций нет. Примеры:<br>
      • квота «Обход» ≥ 100% → переместить из «Обход» в «База» + сообщение «Лимит обхода исчерпан, вернётся {{RD_QUOTA_RESET}}»<br>
      • квота ≥ 80% → сообщение «Осталось {{RD_QUOTA_LEFT}} ГБ обхода»<br>
      • подписка истекает через ≤ 3 дня → сообщение «Продлите подписку в боте»<br>
      • обновил подписку, в User-Agent есть «ward» → +7 дней к подписке (один раз)</p></div>`,
    bind: ({ list, meta }, root) => {
      $("#a-new").addEventListener("click", () => autoForm(null, meta));
      $$("[data-a]", root).forEach((b) => b.addEventListener("click", (e) => {
        const a = list.find((x) => String(x.id) === b.dataset.id);
        const act = b.dataset.a;
        if (act === "edit") return autoForm(a, meta);
        if (act === "log") return autoLog(a).catch((x) => toast(x.message, true));
        if (act === "preview") return busy(e.currentTarget, async () => {
          try {
            const r = await post(`/api/automations/${a.id}/preview`);
            const list2 = (xs) => xs.length ? xs.slice(0, 40).map(esc).join(", ") + (xs.length > 40 ? ` и ещё ${xs.length - 40}` : "") : "никого";
            openSheet({ title: esc(a.name), lead: icon("bolt"), body: `<p>Под условием сейчас: <b>${r.matched}</b></p>
              <p class="muted">Применится к: ${list2(r.would_apply)}</p><p class="muted">Откатится у: ${list2(r.would_revert)}</p>
              <p class="hint">Ничего не изменено. ${a.mode === "active" && a.enabled ? "Автоматизация боевая — это произойдёт при следующем прогоне (раз в 5 минут и сразу после прогона квот)." : "Переключи в «боевой», чтобы применить."}</p>` });
          } catch (x) { toast(x.message, true); }
        });
        if (act === "del") {
          if (!confirm(`Удалить «${a.name}»? Всё, что она сделала со сквадами, откатится.`)) return;
          busy(e.currentTarget, async () => {
            try { const r = await api(`/api/automations/${a.id}`, { method: "DELETE" }); toast(`Удалено, откат у ${r.reverted.length}`); redraw(false); }
            catch (x) { toast(x.message, true); }
          });
        }
      }));
    },
  },
});

/* ============================================================
   Баны по HWID и IP (в прослойке подписок)
   ============================================================ */
let banQuery = "";

async function banAdd(kind, value, note, btn) {
  const run = async () => {
    try { const r = await post("/api/bans", { kind, value, note }); toast(`Забанен ${kind === "hwid" ? "HWID" : "IP"} ${r.value}`); redraw(false); }
    catch (x) { toast(x.message, true); }
  };
  return btn ? busy(btn, run) : run();
}

function banLogRows(rows) {
  if (!rows.length) return `<p class="empty">Ничего не нашлось.</p>`;
  const app = (ua) => esc((ua || "").split(" ")[0].slice(0, 40));
  return `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Пользователь</th><th>IP</th><th>HWID</th><th>Приложение</th><th></th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td class="muted" style="white-space:nowrap">${ago(r.ts)}</td>
      <td>${esc(r.username || "—")}${r.banned ? ` <span class="tag bad">бан</span>` : ""}</td>
      <td class="mono">${esc(r.ip || "")}</td>
      <td class="mono" title="${esc(r.hwid || "")}">${r.hwid ? esc(r.hwid.length > 14 ? r.hwid.slice(0, 14) + "…" : r.hwid) : `<span class="muted">нет</span>`}</td>
      <td class="muted" title="${esc(r.ua || "")}">${app(r.ua)}</td>
      <td style="white-space:nowrap">
        ${r.ip ? `<button class="btn btn-sm btn-ghost" data-ban="ip" data-v="${esc(r.ip)}" data-u="${esc(r.username || "")}">бан IP</button>` : ""}
        ${r.hwid ? `<button class="btn btn-sm btn-ghost btn-danger" data-ban="hwid" data-v="${esc(r.hwid)}" data-u="${esc(r.username || "")}">бан HWID</button>` : ""}
      </td></tr>`).join("")}</tbody></table></div>`;
}

function bindBanButtons(root) {
  $$("[data-ban]", root).forEach((b) => b.addEventListener("click", () => {
    const kind = b.dataset.ban, v = b.dataset.v, u = b.dataset.u;
    const warn = kind === "ip" ? "\n\nIP мобильных операторов делят тысячи людей — бан может задеть чужих. Надёжнее HWID." : "";
    if (!confirm(`Забанить ${kind === "hwid" ? "устройство" : "IP"} ${v}${u ? ` (${u})` : ""}?${warn}`)) return;
    banAdd(kind, v, u ? `с аккаунта ${u}` : "", b);
  }));
}

Object.assign(VIEWS, {
  bans: {
    title: "Баны", sub: "Устройства и адреса, которым прослойка не отдаёт подписку", refresh: 60,
    load: async () => {
      const [d, log] = await Promise.all([api("/api/bans"), api(`/api/bans/log?q=${encodeURIComponent(banQuery)}`)]);
      return { ...d, rows: log };
    },
    draw: (d) => `
      <div class="panel" data-a>
        <p class="muted" style="margin-top:0">Запрос подписки с забаненного HWID или IP в Remnawave не уходит — клиент видит сообщение ниже вместо серверов.
          HWID-бан действует на все аккаунты. <b>Уже скачанные конфиги продолжают работать</b>, пока у пользователя не сменить ключи —
          «Новая подписка» в его карточке. Работает, только когда подписки идут через прослойку.</p>
        <form class="form" id="ban-f" style="max-width:none">
          <div class="form-grid">
            <label>Что банить<select name="kind"><option value="hwid">HWID устройства</option><option value="ip">IP или подсеть</option></select></label>
            <label>Значение<input name="value" required placeholder="HWID, 1.2.3.4 или 1.2.3.0/24"></label>
          </div>
          <label>Заметка<input name="note" maxlength="200" placeholder="за что — необязательно"></label>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Забанить</button></div>
        </form>
      </div>
      <div class="panel" data-a><h2>В бане · ${d.bans.length}</h2>
        ${d.bans.length ? `<div class="table-wrap"><table><thead><tr><th>Тип</th><th>Значение</th><th>Заметка</th><th>Срабатываний</th><th></th></tr></thead>
          <tbody>${d.bans.map((b) => `<tr><td><span class="tag ${b.kind === "hwid" ? "bad" : "warn"}">${b.kind === "hwid" ? "HWID" : "IP"}</span></td>
            <td class="mono" style="word-break:break-all">${esc(b.value)}</td><td class="muted">${esc(b.note || "")}</td>
            <td>${b.hits}${b.last_hit ? ` <span class="muted">· ${ago(b.last_hit)}${b.last_user ? ` · ${esc(b.last_user)}` : ""}</span>` : ""}</td>
            <td><button class="btn btn-sm btn-ghost" data-unban="${b.id}">Снять</button></td></tr>`).join("")}</tbody></table></div>`
          : `<p class="empty">Пусто.</p>`}
      </div>
      <div class="panel" data-a><h2>Что видит забаненный</h2>
        <form class="form" id="ban-msg" style="max-width:none">
          <label><textarea name="message" rows="2" maxlength="300">${esc(d.message)}</textarea>
            <span class="hint">Каждая строка — отдельная «строчка-сервер» в приложении и плашка Happ; в браузере — страница с этим текстом</span></label>
          <div class="form-actions"><button class="btn" type="submit">Сохранить</button></div>
        </form>
      </div>
      <div class="panel" data-a><h2>Обновления подписки <small>${fmtNum(d.log.c)} за ${d.log.since ? ago(d.log.since).replace(" назад", "") : "—"}</small></h2>
        <form class="toolbar" id="ban-q"><div class="search">${icon("search")}<input name="q" value="${esc(banQuery)}" placeholder="Ник, IP, HWID или приложение"></div>
          <button class="btn" type="submit">Найти</button></form>
        <div id="ban-rows">${banLogRows(d.rows)}</div>
      </div>`,
    bind: (d, root) => {
      $("#ban-f").addEventListener("submit", (e) => {
        e.preventDefault();
        const v = Object.fromEntries(new FormData(e.target));
        if (v.kind === "ip" && !confirm("IP мобильных операторов делят тысячи людей — бан может задеть чужих. Продолжить?")) return;
        banAdd(v.kind, v.value.trim(), v.note.trim(), e.submitter);
      });
      $("#ban-msg").addEventListener("submit", (e) => {
        e.preventDefault();
        busy(e.submitter, async () => {
          try { await api("/api/bans/message", { method: "PUT", body: JSON.stringify({ message: new FormData(e.target).get("message") }) }); toast("Сохранено"); }
          catch (x) { toast(x.message, true); }
        });
      });
      $("#ban-q").addEventListener("submit", (e) => {
        e.preventDefault();
        banQuery = new FormData(e.target).get("q");
        busy(e.submitter, async () => {
          $("#ban-rows").innerHTML = banLogRows(await api(`/api/bans/log?q=${encodeURIComponent(banQuery)}`));
          bindBanButtons($("#ban-rows"));
        });
      });
      $$("[data-unban]", root).forEach((b) => b.addEventListener("click", () => busy(b, async () => {
        try { await api(`/api/bans/${b.dataset.unban}`, { method: "DELETE" }); toast("Бан снят"); redraw(false); }
        catch (x) { toast(x.message, true); }
      })));
      bindBanButtons(root);
    },
  },
});
