/* Mailbox front-end (Bootstrap 5.3): list interactions, thread view, compose window, polling, shortcuts. */
(function () {
  "use strict";
  const M = window.MAILBOX || { urls: {} };
  const bs = window.bootstrap;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrf = () => (document.querySelector("meta[name=csrf-token]") || {}).content || "";
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } }
  };

  // ------------------------------------------------------------------ top loading bar
  const bar = $("#top-progress");
  let busy = 0;
  const progress = {
    start() { if (!bar) return; busy++; bar.classList.remove("done"); void bar.offsetWidth; bar.classList.add("active"); },
    stop() { if (!bar) return; busy = Math.max(0, busy - 1); if (!busy) { bar.classList.remove("active"); bar.classList.add("done"); } }
  };
  // show it while the browser loads the next page
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (a && !a.target && !e.ctrlKey && !e.metaKey && !e.shiftKey && a.origin === location.origin &&
        !a.getAttribute("href").startsWith("#") && !a.hasAttribute("download") && !a.closest(".att-chip, .att-card")) progress.start();
  });
  document.addEventListener("submit", (e) => { if (!e.defaultPrevented && !e.target.closest("#compose, #dialog")) progress.start(); });
  window.addEventListener("pageshow", () => { busy = 0; if (bar) bar.className = "top-progress"; });

  async function api(url, { method = "GET", json, form } = {}) {
    progress.start();
    try { return await apiCall(url, { method, json, form }); } finally { progress.stop(); }
  }

  async function apiCall(url, { method = "GET", json, form } = {}) {
    const opts = { method, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" }, credentials: "same-origin" };
    if (json !== undefined) { opts.body = JSON.stringify(json); opts.headers["Content-Type"] = "application/json"; }
    if (form) opts.body = form;
    let res, data = {};
    try {
      res = await fetch(url, opts);
      data = await res.json().catch(() => ({}));
    } catch (e) {
      throw new Error("Network error — check your connection.");
    }
    if (!res.ok || data.ok === false) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  // ------------------------------------------------------------------ theme (light / dark / auto)
  function applyTheme(t) {
    const mode = t === "auto" || !t ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : t;
    document.documentElement.setAttribute("data-bs-theme", mode);
    $$("[data-theme]").forEach(b => b.classList.toggle("active", b.dataset.theme === (t || "auto")));
  }
  applyTheme(store.get("theme") || "auto");
  $$("[data-theme]").forEach(b => b.addEventListener("click", () => { store.set("theme", b.dataset.theme); applyTheme(b.dataset.theme); }));
  $$("[data-theme-cycle]").forEach(b => b.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    store.set("theme", next); applyTheme(next);
  }));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if ((store.get("theme") || "auto") === "auto") applyTheme("auto"); });

  // ------------------------------------------------------------------ density + previews (quick settings)
  function applyDensity(d) {
    document.body.classList.remove("density-comfortable", "density-compact");
    if (d !== "compact") d = "default";
    if (d === "compact") document.body.classList.add("density-compact");
    $$('input[name="density"]').forEach(r => (r.checked = r.value === d));
  }
  applyDensity(store.get("density") || "default");
  $$('input[name="density"]').forEach(r => r.addEventListener("change", () => { store.set("density", r.value); applyDensity(r.value); }));
  const snip = $("#qs-snippets");
  const applySnippets = (on) => { document.body.classList.toggle("no-snippets", !on); if (snip) snip.checked = on; };
  applySnippets(store.get("snippets") !== "0");
  if (snip) snip.addEventListener("change", () => { store.set("snippets", snip.checked ? "1" : "0"); applySnippets(snip.checked); });

  // ------------------------------------------------------------------ toast (Bootstrap Toast)
  const toastEl = $("#toast");
  const toastInst = toastEl && bs ? new bs.Toast(toastEl, { autohide: false }) : null;
  let toastTimer;
  function toast(text, action, ms = 5000) {
    if (!toastInst) return;
    $("#toast-text").textContent = text;
    const btn = $("#toast-action");
    btn.hidden = !action;
    if (action) { btn.textContent = action.label; btn.onclick = () => { toastInst.hide(); action.fn(); }; }
    clearTimeout(toastTimer);
    toastInst.show();
    if (ms) toastTimer = setTimeout(() => toastInst.hide(), ms);
  }

  // ------------------------------------------------------------------ dialogs (Bootstrap Modal)
  const dialogEl = $("#dialog");
  function ask({ title = "Confirm", text = "", ok = "OK", input = null, danger = false } = {}) {
    if (!dialogEl || !bs) return Promise.resolve(input !== null ? window.prompt(text, input) : window.confirm(text));
    return new Promise((resolve) => {
      const modal = bs.Modal.getOrCreateInstance(dialogEl);
      const inp = $("#dialog-input"), okBtn = $("#dialog-ok");
      $("#dialog-title").textContent = title;
      $("#dialog-text").textContent = text;
      inp.hidden = input === null; inp.value = input || "";
      okBtn.textContent = ok;
      okBtn.className = "btn rounded-pill px-4 " + (danger ? "btn-danger" : "btn-primary");
      let result = input !== null ? null : false;
      const form = $("#dialog-form");
      form.onsubmit = (e) => { e.preventDefault(); result = input !== null ? inp.value : true; modal.hide(); };
      dialogEl.addEventListener("shown.bs.modal", () => (input !== null ? inp : okBtn).focus(), { once: true });
      dialogEl.addEventListener("hidden.bs.modal", () => resolve(result), { once: true });
      modal.show();
    });
  }
  // <form data-confirm="..."> → confirm in a modal before submitting
  $$("form[data-confirm]").forEach(f => f.addEventListener("submit", async (e) => {
    if (f.dataset.confirmed) return;
    e.preventDefault();
    const okText = f.dataset.confirmOk || "Remove";
    if (await ask({ title: f.dataset.confirmTitle || "Are you sure?", text: f.dataset.confirm, ok: okText,
                    danger: !/^(activate|unlock|ok|yes)/i.test(okText) })) {
      f.dataset.confirmed = "1"; f.submit();
    }
  }));

  // ------------------------------------------------------------------ sidebar collapse (desktop)
  const menuToggle = $("#menu-toggle");
  if (store.get("navCollapsed") === "1") document.body.classList.add("nav-collapsed");
  if (menuToggle) menuToggle.addEventListener("click", () => {
    document.body.classList.toggle("nav-collapsed");
    store.set("navCollapsed", document.body.classList.contains("nav-collapsed") ? "1" : "0");
  });

  // ------------------------------------------------------------------ advanced search
  const soGo = $("#search-options-go");
  if (soGo) soGo.addEventListener("click", () => {
    const parts = [];
    $$("#search-options [data-op]").forEach(inp => {
      if (inp.type === "checkbox") { if (inp.checked) parts.push(`${inp.dataset.op}:${inp.value}`); return; }
      const v = inp.value.trim(); if (!v) return;
      const val = inp.type === "date" ? v.replaceAll("-", "/") : (/\s/.test(v) && inp.dataset.op ? `"${v}"` : v);
      parts.push(inp.dataset.op ? `${inp.dataset.op}:${val}` : val);
    });
    $("#search-input").value = parts.join(" ");
    $("#search-form").submit();
  });

  // ------------------------------------------------------------------ actions
  const doAction = (ids, action, extra = {}) => api(M.urls.actions, { method: "POST", json: Object.assign({ ids, action }, extra) });
  const REMOVES = new Set(["archive", "trash", "spam", "inbox", "move", "delete_forever"]);

  function setIcon(el, name) {
    const i = el.tagName === "I" ? el : $("i.bi", el);
    if (i) i.className = i.className.replace(/\bbi-[\w-]+/g, "").trim() + " bi-" + name;
  }

  // ------------------------------------------------------------------ message list
  const list = $("#message-list");
  const selectedIds = () => $$(".row-cb:checked").map(cb => cb.value);
  function updateBulk() {
    const ids = selectedIds();
    const bulk = $(".bulk-actions"), idle = $(".idle-actions"), all = $("#select-all");
    if (bulk) bulk.hidden = ids.length === 0;
    if (idle) idle.classList.toggle("d-none", ids.length > 0);
    $$(".mail-row").forEach(r => r.classList.toggle("selected", !!$(".row-cb:checked", r)));
    if (all) {
      const total = $$(".row-cb").length;
      all.checked = total > 0 && ids.length === total;
      all.indeterminate = ids.length > 0 && ids.length < total;
    }
    const cnt = $("#selected-count");
    if (cnt) cnt.textContent = ids.length ? `${ids.length} selected` : "";
  }
  $$("[data-select]").forEach(b => b.addEventListener("click", () => {
    const how = b.dataset.select;
    $$(".mail-row").forEach(r => {
      const cb = $(".row-cb", r); if (!cb) return;
      cb.checked = how === "all" || (how === "read" && !r.classList.contains("unread")) ||
        (how === "unread" && r.classList.contains("unread")) || (how === "starred" && r.classList.contains("starred")) ||
        (how === "unstarred" && !r.classList.contains("starred"));
    });
    updateBulk();
  }));
  async function applyToRows(ids, action, extra) {
    try {
      const res = await doAction(ids, action, extra);
      ids.forEach(id => {
        const row = $(`.mail-row[data-id="${id}"]`); if (!row) return;
        if (REMOVES.has(action)) { row.remove(); return; }
        if (action === "read") row.classList.remove("unread");
        if (action === "unread") row.classList.add("unread");
        const toggle = $('[data-row-action="read"],[data-row-action="unread"]', row);
        if (toggle && (action === "read" || action === "unread")) {
          const nowUnread = action === "unread";
          toggle.dataset.rowAction = nowUnread ? "read" : "unread";
          toggle.title = nowUnread ? "Mark as read" : "Mark as unread";
          setIcon(toggle, nowUnread ? "envelope-open" : "envelope");
        }
      });
      $$(".row-cb:checked").forEach(cb => (cb.checked = false));
      updateBulk();
      toast(res.message);
      refreshCounts();
      if (list && !$(".mail-row", list)) reloadList();
    } catch (e) { toast(e.message); }
  }

  function openRow(row, newTab) {
    if (row.dataset.draft) { Compose.open("draft", row.dataset.draft); return; }
    if (newTab) window.open(row.dataset.href, "_blank"); else window.location.href = row.dataset.href;
  }

  if (list) {
    list.addEventListener("click", (e) => {
      if (e.target.closest(".row-check")) return;
      // phones: tapping the avatar selects the conversation (like Gmail's app)
      const av = e.target.closest(".row-avatar");
      if (av && window.matchMedia("(max-width: 767.98px)").matches) {
        const cb = $(".row-cb", av.closest(".mail-row")); cb.checked = !cb.checked; updateBulk(); return;
      }
      const star = e.target.closest(".star");
      if (star) { e.stopPropagation(); toggleStar(star, false); return; }
      const ra = e.target.closest("[data-row-action]");
      if (ra) {
        e.stopPropagation();
        const row = ra.closest(".mail-row");
        if (ra.dataset.rowAction === "discard") { discardDraft(row.dataset.id, row); return; }
        if (ra.dataset.rowAction === "delete_forever") {
          ask({ title: "Delete forever?", text: "This conversation will be permanently deleted from the server.", ok: "Delete forever", danger: true })
            .then(ok => ok && applyToRows([row.dataset.id], "delete_forever"));
          return;
        }
        applyToRows([row.dataset.id], ra.dataset.rowAction);
        return;
      }
      const row = e.target.closest(".mail-row");
      if (row) openRow(row, e.ctrlKey || e.metaKey);
    });
    list.addEventListener("keydown", (e) => {
      const row = e.target.closest(".mail-row");
      if (row && e.target === row && e.key === "Enter") openRow(row, false);
    });
    list.addEventListener("change", (e) => { if (e.target.matches(".row-cb")) updateBulk(); });
    const all = $("#select-all");
    if (all) all.addEventListener("change", () => { $$(".row-cb").forEach(cb => (cb.checked = all.checked)); updateBulk(); });
    $$(".bulk-actions [data-action]").forEach(btn => btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const ids = selectedIds(); if (!ids.length) return;
      if (btn.dataset.action === "delete_forever" &&
          !(await ask({ title: "Delete forever?", text: `Permanently delete ${ids.length} conversation(s) from the server?`, ok: "Delete forever", danger: true }))) return;
      applyToRows(ids, btn.dataset.action, btn.dataset.folder ? { folder_id: btn.dataset.folder } : {});
    }));
    const readAll = $('[data-bulk="read-all"]');
    if (readAll) readAll.addEventListener("click", () => {
      const ids = $$(".mail-row.unread").map(r => r.dataset.id);
      if (ids.length) applyToRows(ids, "read"); else toast("No unread conversations on this page");
    });
    const refresh = $("#refresh-btn");
    if (refresh) refresh.addEventListener("click", () => syncNow(true));
  }

  async function discardDraft(id, row) {
    try {
      await api(`/api/draft/${id}/discard/`, { method: "POST" });
      if (row) row.remove();
      toast("Draft discarded"); refreshCounts();
    } catch (e) { toast(e.message); }
  }

  async function toggleStar(btn, single) {
    const on = !btn.classList.contains("on");
    const paint = (v) => {
      btn.classList.toggle("on", v); setIcon(btn, v ? "star-fill" : "star"); btn.title = v ? "Starred" : "Not starred";
      const row = btn.closest(".mail-row"); if (row) row.classList.toggle("starred", v);
      btn.classList.remove("pop"); void btn.offsetWidth; if (v) btn.classList.add("pop");
    };
    paint(on);
    try {
      await doAction([btn.dataset.id], on ? "star" : "unstar", { thread: !single && !btn.dataset.single });
    } catch (e) { paint(!on); toast(e.message); }
  }

  async function reloadList() {
    if (!list) return;
    const url = new URL(window.location.href);
    url.searchParams.set("partial", "1");
    try {
      const res = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } });
      if (!res.ok) return;
      const tmp = document.createElement("div");
      tmp.innerHTML = await res.text();
      const fresh = $("#message-list", tmp);
      if (fresh) { list.innerHTML = fresh.innerHTML; updateBulk(); }
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ sync & counts
  let syncing = false;
  async function syncNow(manual) {
    if (syncing) return;
    syncing = true;
    const status = $("#sync-status"); const btn = $("#refresh-btn");
    if (btn) btn.classList.add("spinning");
    if (status) status.textContent = "Checking mail…";
    const fd = new FormData(); if (M.folderId) fd.append("folder_id", M.folderId);
    try {
      const res = await api(M.urls.sync, { method: "POST", form: fd });
      if (status) status.textContent = res.busy ? "Sync in progress…" : (res.new ? `${res.new} new` : "Up to date");
      await refreshCounts();
      if (list && !selectedIds().length) await reloadList();
    } catch (e) {
      if (status) status.textContent = "";
      if (manual) toast(e.message);
    } finally {
      syncing = false; if (btn) btn.classList.remove("spinning");
      setTimeout(() => { if (status) status.textContent = ""; }, 4000);
    }
  }

  let latestInbox = null;
  async function refreshCounts() {
    if (!M.urls.counts || !M.accountId) return;
    try {
      const res = await fetch(M.urls.counts, { credentials: "same-origin" }).then(r => r.json());
      $$(".folder-nav .nav-link[data-folder-id]").forEach(a => {
        const f = res.folders[a.dataset.folderId]; if (!f) return;
        const n = a.dataset.key === "drafts" ? f.total : (["inbox", "spam", "label"].includes(a.dataset.key) ? f.unread : 0);
        $(".nav-count", a).textContent = n ? n : "";
        a.classList.toggle("has-unread", !!n && a.dataset.key !== "drafts");
      });
      const inbox = Object.values(res.folders).find(f => f.role === "inbox");
      document.title = document.title.replace(/^\(\d+\)\s*/, "");
      if (inbox && inbox.unread) document.title = `(${inbox.unread}) ${document.title}`;
      if (latestInbox !== null && res.latest_inbox_id > latestInbox) {
        const onFirstPage = list && (list.dataset.page === "1" || !list.dataset.page);
        if (onFirstPage && !selectedIds().length && M.view !== "search") reloadList();
        else toast("New mail has arrived", { label: "Show", fn: () => (window.location.href = "/mail/inbox/") });
      }
      latestInbox = res.latest_inbox_id;
    } catch (e) { /* offline */ }
  }
  if (M.accountId) {
    refreshCounts();
    setInterval(refreshCounts, 20000);
    // While the first sync is still downloading, keep refreshing the empty list.
    if (list && !$(".mail-row", list)) {
      let tries = 0;
      const t = setInterval(() => { if ($(".mail-row", list) || ++tries > 30) clearInterval(t); else reloadList(); }, 5000);
    }
  }

  // ------------------------------------------------------------------ thread view
  const thread = $("#thread");
  function sizeFrame(frame) {
    try {
      const doc = frame.contentDocument; if (!doc || !doc.body) return;
      const fit = () => { frame.style.height = Math.max(40, doc.documentElement.scrollHeight) + "px"; };
      fit();
      if (window.ResizeObserver) new ResizeObserver(fit).observe(doc.body);
      $$("img", doc).forEach(img => img.addEventListener("load", fit));
    } catch (e) { frame.style.height = "600px"; }
  }
  function loadFrame(msgEl) {
    const frame = $(".body-frame", msgEl);
    if (frame && !frame.src) { frame.addEventListener("load", () => sizeFrame(frame)); frame.src = frame.dataset.src; }
  }
  if (thread) {
    $$(".msg.expanded", thread).forEach(loadFrame);
    const ids = thread.dataset.ids.split(",");
    thread.addEventListener("click", async (e) => {
      const star = e.target.closest(".star");
      if (star) { e.stopPropagation(); toggleStar(star, true); return; }
      const det = e.target.closest(".details-btn");
      if (det) { e.stopPropagation(); const t = $(".msg-details", det.closest(".msg")); t.hidden = !t.hidden; return; }
      const comp = e.target.closest("[data-compose]");
      if (comp) { e.stopPropagation(); Compose.open(comp.dataset.compose, comp.dataset.id); return; }
      const ma = e.target.closest("[data-msg-action]");
      if (ma) {
        e.stopPropagation();
        try {
          const res = await doAction([ma.dataset.id], ma.dataset.msgAction, { thread: false });
          toast(res.message);
          if (ma.dataset.msgAction === "unread") window.location.href = thread.dataset.back;
          else { ma.closest(".msg").remove(); if (!$(".msg", thread)) window.location.href = thread.dataset.back; }
        } catch (err) { toast(err.message); }
        return;
      }
      const ta = e.target.closest("[data-thread-action]");
      if (ta) {
        e.stopPropagation();
        const action = ta.dataset.threadAction;
        if (action === "delete_forever" &&
            !(await ask({ title: "Delete forever?", text: "This conversation will be permanently deleted from the server.", ok: "Delete forever", danger: true }))) return;
        try {
          const res = await doAction(ids, action, Object.assign({ thread: true }, ta.dataset.folder ? { folder_id: ta.dataset.folder } : {}));
          store.set("flash", res.message);
          window.location.href = thread.dataset.back;
        } catch (err) { toast(err.message); }
        return;
      }
      const head = e.target.closest(".msg-head");
      if (head && !e.target.closest("button, a, .dropdown")) {
        const msg = head.closest(".msg");
        msg.classList.toggle("collapsed"); msg.classList.toggle("expanded");
        if (msg.classList.contains("expanded")) loadFrame(msg);
      }
    });
    const pb = $("#print-btn");
    if (pb) pb.addEventListener("click", () => {
      $$(".msg", thread).forEach(m => { m.classList.add("expanded"); m.classList.remove("collapsed"); loadFrame(m); });
      setTimeout(() => window.print(), 600);
    });
  }
  const flashMsg = store.get("flash");
  if (flashMsg) { store.set("flash", ""); toast(flashMsg); }

  // ------------------------------------------------------------------ compose
  const Compose = (function () {
    const box = $("#compose");
    if (!box) return { open() {}, close() {}, isOpen: () => false };
    const form = $("#compose-form");
    const body = $("#c-body");
    const title = $("#compose-title");
    let files = [];        // File objects added by the user
    let keep = [];         // {id, name, size} attachments carried over (forward / draft)
    let dirty = false, sending = false;

    const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
    function renderAtts() {
      const wrap = $("#c-attachments"); wrap.innerHTML = "";
      const add = (name, size, onRemove) => {
        const el = document.createElement("span");
        el.className = "badge rounded-pill text-bg-light border d-inline-flex align-items-center gap-1 fw-normal py-1 ps-3 pe-1";
        el.innerHTML = `<i class="bi bi-paperclip"></i><span class="name text-primary fw-semibold text-truncate" style="max-width:240px"></span>` +
          `<span class="text-body-secondary">(${fmtSize(size)})</span><button type="button" class="btn-close btn-sm ms-1" aria-label="Remove"></button>`;
        $(".name", el).textContent = name;
        $("button", el).addEventListener("click", () => { onRemove(); dirty = true; renderAtts(); });
        wrap.appendChild(el);
      };
      keep.forEach((a, i) => add(a.name, a.size, () => keep.splice(i, 1)));
      files.forEach((f, i) => add(f.name, f.size, () => files.splice(i, 1)));
    }

    function reset() {
      form.reset(); body.innerHTML = ""; files = []; keep = []; dirty = false;
      $("#c-cc-row").hidden = true; $("#c-bcc-row").hidden = true;
      $("#c-in-reply-to").value = ""; $("#c-draft-id").value = ""; $("#c-status").textContent = "";
      renderAtts();
    }

    async function open(mode = "new", id = "") {
      if (!box.hidden && dirty) await saveDraft(true);
      reset();
      box.hidden = false; box.classList.remove("minimized");
      title.textContent = mode === "forward" ? "Forward" : mode.startsWith("reply") ? "Reply" : mode === "draft" ? "Draft" : "New Message";
      try {
        const d = await api(`${M.urls.composeData}?${new URLSearchParams({ mode, id })}`);
        $("#c-to").value = d.to || "";
        $("#c-cc").value = d.cc || ""; $("#c-bcc").value = d.bcc || "";
        if (d.cc) $("#c-cc-row").hidden = false;
        if (d.bcc) $("#c-bcc-row").hidden = false;
        $("#c-subject").value = d.subject || "";
        body.innerHTML = d.body || "";
        $("#c-in-reply-to").value = d.in_reply_to || "";
        $("#c-draft-id").value = d.draft_id || "";
        if (d.account_id && $("#c-account")) $("#c-account").value = d.account_id;
        keep = d.attachments || [];
        renderAtts();
        if (d.subject) title.textContent = d.subject;
      } catch (e) { toast(e.message); }
      dirty = false;
      if (!$("#c-to").value) $("#c-to").focus();
      else { body.focus(); caretToStart(body); }
    }

    function caretToStart(el) {
      const r = document.createRange(); r.setStart(el, 0); r.collapse(true);
      const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    }

    function formData() {
      const fd = new FormData(form);
      fd.set("body", body.innerHTML);
      files.forEach(f => fd.append("attachments", f));
      keep.forEach(a => fd.append("keep_attachments", a.id));
      return fd;
    }
    const hasContent = () => $("#c-to").value.trim() || $("#c-subject").value.trim() || body.textContent.trim() || files.length;

    async function saveDraft(silent) {
      if (!hasContent()) return;
      $("#c-status").textContent = "Saving…";
      try {
        const res = await api(M.urls.draft, { method: "POST", form: formData() });
        $("#c-draft-id").value = res.draft_id;
        // the saved draft now owns the uploaded files; carry them over as kept attachments
        keep = res.attachments || []; files = []; renderAtts();
        dirty = false;
        $("#c-status").textContent = "Draft saved";
        if (!silent) toast("Draft saved");
        refreshCounts();
        return res;
      } catch (e) { $("#c-status").textContent = ""; toast("Could not save draft: " + e.message); }
    }

    async function close() {
      if (dirty && hasContent()) await saveDraft(false);
      box.hidden = true; box.classList.remove("maximized"); reset();
    }

    async function send() {
      if (sending) return;
      const to = $("#c-to").value.trim(), cc = $("#c-cc").value.trim(), bcc = $("#c-bcc").value.trim();
      if (!to && !cc && !bcc) { toast("Please specify at least one recipient."); $("#c-to").focus(); return; }
      if (!$("#c-subject").value.trim() &&
          !(await ask({ title: "No subject", text: "Send this message without a subject?", ok: "Send" }))) return;
      sending = true;
      const fd = formData();
      box.hidden = true;
      let cancelled = false;
      // Gmail-style "Undo send": wait a few seconds before actually sending.
      toast("Sending…", { label: "Undo", fn: () => { cancelled = true; box.hidden = false; sending = false; toast("Sending undone", null, 2500); } }, 0);
      await new Promise(r => setTimeout(r, 5000));
      if (cancelled) return;
      toast("Sending…", null, 0);
      try {
        const res = await api(M.urls.send, { method: "POST", form: fd });
        toast(res.message || "Message sent");
        reset(); sending = false;
        refreshCounts();
        if (M.view === "sent" || M.view === "drafts") reloadList();
      } catch (e) {
        sending = false; box.hidden = false;
        toast(e.message, null, 8000);
      }
    }

    form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
    form.addEventListener("input", () => { dirty = true; });
    body.addEventListener("input", () => { dirty = true; });
    body.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); send(); } });
    $("#compose-close").addEventListener("click", (e) => { e.stopPropagation(); close(); });
    $("#compose-min").addEventListener("click", (e) => { e.stopPropagation(); box.classList.toggle("minimized"); box.classList.remove("maximized"); });
    $("#compose-max").addEventListener("click", (e) => { e.stopPropagation(); box.classList.toggle("maximized"); box.classList.remove("minimized"); });
    $("#compose-head").addEventListener("click", () => box.classList.remove("minimized"));
    $("#c-show-cc").addEventListener("click", () => { $("#c-cc-row").hidden = false; $("#c-cc").focus(); });
    $("#c-show-bcc").addEventListener("click", () => { $("#c-bcc-row").hidden = false; $("#c-bcc").focus(); });
    $("#c-files").addEventListener("change", (e) => { files.push(...e.target.files); e.target.value = ""; dirty = true; renderAtts(); });
    $("#c-format-toggle").addEventListener("click", () => { $("#c-format").hidden = !$("#c-format").hidden; });
    $$("#c-format [data-cmd]").forEach(b => b.addEventListener("mousedown", (e) => {
      e.preventDefault();
      document.execCommand(b.dataset.cmd, false, b.dataset.arg || null);
      dirty = true;
    }));
    $("#c-link").addEventListener("mousedown", async (e) => {
      e.preventDefault();
      const sel = window.getSelection();
      const range = sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
      const url = await ask({ title: "Insert link", text: "Web address (https://…) or mailto:", input: "https://", ok: "Insert" });
      if (url && /^(https?:|mailto:)/i.test(url)) {
        body.focus();
        if (range && body.contains(range.commonAncestorContainer)) { sel.removeAllRanges(); sel.addRange(range); }
        if (sel.isCollapsed) document.execCommand("insertHTML", false, `<a href="${url.replace(/"/g, "&quot;")}">${url.replace(/</g, "&lt;")}</a>`);
        else document.execCommand("createLink", false, url);
        dirty = true;
      }
    });
    $("#c-discard").addEventListener("click", async () => {
      const id = $("#c-draft-id").value;
      box.hidden = true; reset();
      if (id) await discardDraft(id, $(`.mail-row[data-id="${id}"]`)); else toast("Draft discarded");
    });
    box.addEventListener("dragover", (e) => { e.preventDefault(); });
    box.addEventListener("drop", (e) => {
      if (!e.dataTransfer.files.length) return;
      e.preventDefault(); files.push(...e.dataTransfer.files); dirty = true; renderAtts();
    });

    // ---- address autocomplete
    const sug = $("#c-suggest");
    let sugInput = null, sugItems = [], sugIdx = 0, sugTimer;
    function hideSug() { sug.hidden = true; sugItems = []; }
    function pick(i) {
      const it = sugItems[i]; if (!it || !sugInput) return;
      const parts = sugInput.value.split(",");
      parts[parts.length - 1] = " " + (it.name ? `${it.name} <${it.email}>` : it.email);
      sugInput.value = parts.join(",").replace(/^\s+/, "") + ", ";
      dirty = true; hideSug(); sugInput.focus();
    }
    function showSug(input, items) {
      sugInput = input; sugItems = items; sugIdx = 0;
      if (!items.length) return hideSug();
      sug.innerHTML = "";
      items.forEach((it, i) => {
        const d = document.createElement("button");
        d.type = "button";
        d.className = "list-group-item list-group-item-action" + (i === 0 ? " active" : "");
        d.innerHTML = `<span class="avatar text-bg-primary"></span><div class="text-start overflow-hidden"><div class="n text-truncate"></div><small class="e text-truncate d-block opacity-75"></small></div>`;
        $(".avatar", d).textContent = (it.name || it.email)[0].toUpperCase();
        $(".n", d).textContent = it.name || it.email; $(".e", d).textContent = it.name ? it.email : "";
        d.addEventListener("mousedown", (e) => { e.preventDefault(); pick(i); });
        sug.appendChild(d);
      });
      const r = input.getBoundingClientRect();
      sug.style.left = r.left + "px"; sug.style.top = r.bottom + 2 + "px";
      sug.hidden = false;
    }
    $$(".addr-input", form).forEach(inp => {
      inp.addEventListener("input", () => {
        clearTimeout(sugTimer);
        const term = inp.value.split(",").pop().trim();
        if (term.length < 1) return hideSug();
        sugTimer = setTimeout(async () => {
          try {
            const d = await fetch(`${M.urls.contacts}?q=${encodeURIComponent(term)}`, { credentials: "same-origin" }).then(r => r.json());
            showSug(inp, d.results || []);
          } catch (e) { hideSug(); }
        }, 150);
      });
      inp.addEventListener("keydown", (e) => {
        if (sug.hidden) return;
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          sugIdx = (sugIdx + (e.key === "ArrowDown" ? 1 : -1) + sugItems.length) % sugItems.length;
          $$(".list-group-item", sug).forEach((el, i) => el.classList.toggle("active", i === sugIdx));
        } else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(sugIdx); }
        else if (e.key === "Escape") hideSug();
      });
      inp.addEventListener("blur", () => setTimeout(hideSug, 150));
    });

    setInterval(() => { if (!box.hidden && dirty && !sending && hasContent()) saveDraft(true); }, 30000);
    window.addEventListener("beforeunload", (e) => { if (!box.hidden && dirty && hasContent()) { e.preventDefault(); e.returnValue = ""; } });

    return { open, close, isOpen: () => !box.hidden };
  })();

  const composeBtn = $("#compose-btn");
  if (composeBtn) composeBtn.addEventListener("click", () => {
    const oc = bs && bs.Offcanvas.getInstance($("#sidebar"));
    if (oc) oc.hide();
    Compose.open("new");
  });
  const params = new URLSearchParams(window.location.search);
  if (params.get("draft")) Compose.open("draft", params.get("draft"));
  if (params.get("compose")) Compose.open("new");

  // ------------------------------------------------------------------ keyboard shortcuts
  let kbIndex = -1, gPending = 0;
  const rows = () => $$(".mail-row");
  function focusRow(i) {
    const r = rows(); if (!r.length) return;
    kbIndex = Math.max(0, Math.min(r.length - 1, i));
    r.forEach((el, n) => el.classList.toggle("kb-focus", n === kbIndex));
    r[kbIndex].focus({ preventScroll: true });
    r[kbIndex].scrollIntoView({ block: "nearest" });
  }
  const current = () => rows()[kbIndex] || null;
  const targetIds = () => { const ids = selectedIds(); const c = current(); return ids.length ? ids : (c ? [c.dataset.id] : []); };

  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || e.ctrlKey || e.metaKey || e.altKey) {
      if (e.key === "Escape" && Compose.isOpen() && t.closest && t.closest("#compose")) Compose.close();
      return;
    }
    if (document.querySelector(".modal.show")) return;
    const lastId = thread ? $$(".msg", thread).pop()?.dataset.id : null;
    if (gPending && Date.now() - gPending < 1200) {
      gPending = 0;
      const dest = { i: "inbox", s: "starred", t: "sent", d: "drafts", a: "all" }[e.key];
      if (dest) { e.preventDefault(); progress.start(); window.location.href = `/mail/${dest}/`; }
      return;
    }
    switch (e.key) {
      case "?": e.preventDefault(); bs && bs.Modal.getOrCreateInstance($("#shortcuts")).show(); break;
      case "g": gPending = Date.now(); break;
      case "c": e.preventDefault(); Compose.open("new"); break;
      case "/": e.preventDefault(); $("#search-input")?.focus(); break;
      case "j": if (list) { e.preventDefault(); focusRow(kbIndex + 1); } break;
      case "k": if (list) { e.preventDefault(); focusRow(kbIndex < 0 ? 0 : kbIndex - 1); } break;
      case "o": case "Enter": if (list && current()) { e.preventDefault(); openRow(current(), false); } break;
      case "x": if (list && current()) { e.preventDefault(); const cb = $(".row-cb", current()); cb.checked = !cb.checked; updateBulk(); } break;
      case "s": {
        const star = thread ? $$(".msg .star", thread).pop() : (current() && $(".star", current()));
        if (star) { e.preventDefault(); toggleStar(star, !!thread); }
        break;
      }
      case "I": if (list && targetIds().length) { e.preventDefault(); applyToRows(targetIds(), "read"); } break;
      case "U": if (list && targetIds().length) { e.preventDefault(); applyToRows(targetIds(), "unread"); } break;
      case "r": if (lastId) { e.preventDefault(); Compose.open("reply", lastId); } break;
      case "a": if (lastId) { e.preventDefault(); Compose.open("replyall", lastId); } break;
      case "f": if (lastId) { e.preventDefault(); Compose.open("forward", lastId); } break;
      case "u": if (thread) { progress.start(); window.location.href = thread.dataset.back; } break;
      case "e":
        if (thread) $('[data-thread-action="archive"]')?.click();
        else if (list && targetIds().length && $('.bulk-actions [data-action="archive"]')) applyToRows(targetIds(), "archive");
        break;
      case "#":
        if (thread) $('[data-thread-action="trash"]')?.click();
        else if (list && targetIds().length) {
          const ids = targetIds();
          if (M.view !== "trash") applyToRows(ids, "trash");
          else ask({ title: "Delete forever?", text: `Permanently delete ${ids.length} conversation(s) from the server?`, ok: "Delete forever", danger: true })
            .then(okd => okd && applyToRows(ids, "delete_forever"));
        }
        break;
    }
  });
})();
