(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf"]').content;
  const state = { bills: [], accounts: [], sel: null, sort: { k: "due_date", dir: 1 }, imp: null };

  async function api(method, url, body) {
    const r = await fetch(url, {
      method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) {
      if (r.status === 401) { location.href = "/login"; }
      throw new Error(data.error || "Something went wrong (" + r.status + ")");
    }
    return data;
  }

  // ---------- dates and money (local time: "today" is the visitor's own date)
  const pad = (n) => String(n).padStart(2, "0");
  const todayISO = () => { const d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
  const dayNum = (iso) => { const [y, m, d] = iso.split("-").map(Number); return Math.round(Date.UTC(y, m - 1, d) / 864e5); };
  const daysUntil = (iso) => dayNum(iso) - dayNum(todayISO());
  const mdy = (iso) => { if (!iso) return ""; const [y, m, d] = iso.split("-"); return Number(m) + "/" + Number(d) + "/" + y; };
  const toCents = (s) => Math.round(parseFloat(s) * 100);
  const usd = (s) => {
    if (s === null || s === undefined || s === "") return "";
    const n = typeof s === "number" ? s : parseFloat(s);
    const t = "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return n < 0 ? "(" + t + ")" : t;
  };
  const unitText = (v, u) => v + " " + u.charAt(0).toUpperCase() + u.slice(1);
  const colorOf = (iso) => { const n = daysUntil(iso); return n < 0 ? "c-red" : n <= 14 ? "c-yellow" : "c-green"; };

  // ---------- dialogs
  function openDlg(d) { if (!d.open) d.showModal(); }
  document.querySelectorAll("dialog").forEach((d) => {
    d.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) d.close(); });
  });

  // ---------- table
  function sortVal(b, k) {
    if (k === "freq") return b.freq_unit === "day" ? b.freq_value : b.freq_unit === "week" ? b.freq_value * 7 : b.freq_unit === "month" ? b.freq_value * 30 : b.freq_value * 365;
    if (k === "amount_due" || k === "last_paid_amount" || k === "balance") return b[k] === null || b[k] === "" ? -Infinity : parseFloat(b[k]);
    if (k === "track_balance") return b.track_balance ? 1 : 0;
    return (b[k] || "").toString().toLowerCase();
  }
  function visible() {
    const q = $("q").value.trim().toLowerCase(), a = $("fAcct").value;
    let rows = state.bills.filter((b) => (!q || b.name.toLowerCase().includes(q)) && (!a || String(b.account_id) === a));
    const { k, dir } = state.sort;
    rows.sort((x, y) => { const p = sortVal(x, k), q2 = sortVal(y, k); return (p < q2 ? -1 : p > q2 ? 1 : 0) * dir || (x.due_date < y.due_date ? -1 : 1); });
    return rows;
  }
  function cell(tr, text, cls) { const td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); return td; }

  function render() {
    const rows = visible(), body = $("billBody");
    body.replaceChildren();
    for (const b of rows) {
      const tr = document.createElement("tr");
      tr.dataset.id = b.id; tr.tabIndex = 0;
      tr.setAttribute("aria-selected", String(b.id === state.sel));
      cell(tr, b.name); cell(tr, b.account);
      cell(tr, usd(b.amount_due), "n"); cell(tr, mdy(b.due_date), colorOf(b.due_date));
      cell(tr, mdy(b.last_paid_date)); cell(tr, unitText(b.freq_value, b.freq_unit));
      cell(tr, mdy(b.next_due)); cell(tr, usd(b.last_paid_amount), "n");
      const neg = b.balance !== null && parseFloat(b.balance) < 0;
      cell(tr, b.track_balance ? usd(b.balance) : "", "n" + (neg ? " neg" : ""));
      cell(tr, b.track_balance ? "Yes" : "No");
      body.appendChild(tr);
    }
    $("empty").hidden = state.bills.length > 0;
    $("count").textContent = rows.length === state.bills.length ? state.bills.length + " bills" : rows.length + " of " + state.bills.length + " bills";
    document.querySelectorAll("#billTable th[data-k]").forEach((th) => {
      if (th.dataset.k === state.sort.k) th.setAttribute("aria-sort", state.sort.dir > 0 ? "ascending" : "descending"); else th.removeAttribute("aria-sort");
    });
    const has = state.sel !== null && state.bills.some((b) => b.id === state.sel);
    ["btnEdit", "btnPay", "btnHist", "btnDelete"].forEach((id) => { $(id).disabled = !has; });
    summary();
  }

  function summary() {
    let late = [0, 0], soon = [0, 0], later = [0, 0], bal = [0, 0];
    for (const b of state.bills) {
      const n = daysUntil(b.due_date), c = toCents(b.amount_due) || 0;
      const g = n < 0 ? late : n <= 14 ? soon : later;
      g[0]++; g[1] += c;
      if (b.track_balance && b.balance !== null) { bal[0]++; bal[1] += toCents(b.balance); }
    }
    $("sumLate").textContent = late[0]; $("sumLateAmt").textContent = late[0] ? usd(late[1] / 100) : "";
    $("sumSoon").textContent = soon[0]; $("sumSoonAmt").textContent = soon[0] ? usd(soon[1] / 100) : "";
    $("sumLater").textContent = later[0]; $("sumLaterAmt").textContent = later[0] ? usd(later[1] / 100) : "";
    $("sumBal").textContent = usd(bal[1] / 100); $("sumBalN").textContent = bal[0] ? bal[0] + " bill" + (bal[0] === 1 ? "" : "s") : "";
  }

  async function load() {
    [state.bills, state.accounts] = await Promise.all([api("GET", "/api/bills"), api("GET", "/api/accounts")]);
    fillAcctSelects();
    render();
  }
  function fillAcctSelects() {
    const f = $("fAcct"), keep = f.value;
    f.replaceChildren(new Option("All accounts", ""));
    state.accounts.forEach((a) => f.appendChild(new Option(a.name, a.id)));
    f.value = state.accounts.some((a) => String(a.id) === keep) ? keep : "";
    const b = $("b_acct"), kb = b.value;
    b.replaceChildren(new Option("(none)", ""));
    state.accounts.forEach((a) => b.appendChild(new Option(a.name, a.id)));
    b.value = kb;
  }
  const selected = () => state.bills.find((b) => b.id === state.sel);

  $("billBody").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) { state.sel = Number(tr.dataset.id); render(); } });
  $("billBody").addEventListener("dblclick", (e) => { if (e.target.closest("tr")) openBill(selected()); });
  $("billBody").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr"); if (!tr) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); state.sel = Number(tr.dataset.id); render(); }
  });
  $("billTable").querySelector("thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]"); if (!th) return;
    state.sort = { k: th.dataset.k, dir: state.sort.k === th.dataset.k ? -state.sort.dir : 1 };
    render();
  });
  $("q").addEventListener("input", render);
  $("fAcct").addEventListener("change", render);

  // ---------- add / update
  let editing = null;
  function openBill(b) {
    editing = b || null;
    $("billTitle").textContent = b ? "Update bill" : "Add new bill";
    $("billErr").textContent = "";
    $("b_name").value = b ? b.name : "";
    $("b_acct").value = b && b.account_id ? b.account_id : (state.accounts[0] ? state.accounts[0].id : "");
    $("b_amt").value = b ? b.amount_due : "";
    $("b_due").value = b ? b.due_date : todayISO();
    $("b_fv").value = b ? b.freq_value : 1;
    $("b_fu").value = b ? b.freq_unit : "month";
    $("b_lpd").value = b && b.last_paid_date ? b.last_paid_date : "";
    $("b_lpa").value = b && b.last_paid_amount ? b.last_paid_amount : "";
    $("b_track").checked = !!(b && b.track_balance);
    $("b_bal").value = b && b.balance !== null ? b.balance : "";
    $("b_balWrap").hidden = !$("b_track").checked;
    openDlg($("billDlg")); $("b_name").focus();
  }
  $("b_track").addEventListener("change", () => { $("b_balWrap").hidden = !$("b_track").checked; });
  $("btnAdd").addEventListener("click", () => openBill(null));
  $("btnEdit").addEventListener("click", () => openBill(selected()));
  $("billForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      name: $("b_name").value, account_id: $("b_acct").value, amount_due: $("b_amt").value, due_date: $("b_due").value,
      freq_value: $("b_fv").value, freq_unit: $("b_fu").value, last_paid_date: $("b_lpd").value, last_paid_amount: $("b_lpa").value,
      track_balance: $("b_track").checked, balance: $("b_bal").value,
    };
    try {
      const saved = editing ? await api("PUT", "/api/bills/" + editing.id, body) : await api("POST", "/api/bills", body);
      state.sel = saved.id; $("billDlg").close(); await load();
    } catch (err) { $("billErr").textContent = err.message; }
  });

  // ---------- delete
  $("btnDelete").addEventListener("click", async () => {
    const b = selected(); if (!b) return;
    if (!confirm("Delete \"" + b.name + "\" and its payment history? This cannot be undone.")) return;
    try { await api("DELETE", "/api/bills/" + b.id); state.sel = null; await load(); } catch (err) { alert(err.message); }
  });

  // ---------- mark as paid
  $("btnPay").addEventListener("click", () => {
    const b = selected(); if (!b) return;
    $("payErr").textContent = "";
    $("payInfo").textContent = b.name + " - due " + mdy(b.due_date) + ", " + usd(b.amount_due);
    $("p_date").value = todayISO(); $("p_amt").value = b.amount_due; $("p_adv").checked = true;
    const note = () => {
      const bits = [];
      if ($("p_adv").checked) bits.push("The next due date will be " + mdy(b.next_due) + ".");
      if (b.track_balance && $("p_amt").value !== "") bits.push("The balance goes from " + usd(b.balance) + " to " + usd((toCents(b.balance) - toCents($("p_amt").value)) / 100) + ".");
      $("payNote").textContent = bits.join(" ");
    };
    $("p_adv").onchange = note; $("p_amt").oninput = note; note();
    openDlg($("payDlg")); $("p_amt").select();
  });
  $("payForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = selected(); if (!b) return;
    try {
      await api("POST", "/api/bills/" + b.id + "/pay", { paid_date: $("p_date").value, amount: $("p_amt").value, advance: $("p_adv").checked });
      $("payDlg").close(); await load();
    } catch (err) { $("payErr").textContent = err.message; }
  });

  // ---------- history
  async function loadHistory() {
    const b = selected(); if (!b) return;
    $("histErr").textContent = "";
    const rows = await api("GET", "/api/bills/" + b.id + "/payments");
    $("histTitle").textContent = "Payment history - " + b.name;
    const body = $("histBody"); body.replaceChildren();
    rows.forEach((p) => { const tr = document.createElement("tr"); cell(tr, mdy(p.paid_date)); cell(tr, usd(p.amount), "n"); cell(tr, mdy(p.due_before)); body.appendChild(tr); });
    $("histEmpty").hidden = rows.length > 0; $("btnUndo").disabled = rows.length === 0;
  }
  $("btnHist").addEventListener("click", async () => { try { await loadHistory(); openDlg($("histDlg")); } catch (err) { alert(err.message); } });
  $("btnUndo").addEventListener("click", async () => {
    const b = selected(); if (!b) return;
    if (!confirm("Undo the latest payment for \"" + b.name + "\"? Its due date and balance go back to how they were.")) return;
    try { await api("POST", "/api/bills/" + b.id + "/undo"); await load(); await loadHistory(); } catch (err) { $("histErr").textContent = err.message; }
  });

  // ---------- accounts
  function renderAccts() {
    const ul = $("acctList"); ul.replaceChildren();
    state.accounts.forEach((a) => {
      const li = document.createElement("li");
      const inp = document.createElement("input"); inp.value = a.name; inp.maxLength = 60; inp.setAttribute("aria-label", "Account name");
      const used = document.createElement("span"); used.className = "used"; used.textContent = a.bills + " bill" + (a.bills === 1 ? "" : "s");
      const save = document.createElement("button"); save.type = "button"; save.textContent = "Rename";
      const del = document.createElement("button"); del.type = "button"; del.textContent = "Delete"; del.className = "danger";
      save.addEventListener("click", async () => { try { $("acctErr").textContent = ""; await api("PUT", "/api/accounts/" + a.id, { name: inp.value }); await load(); renderAccts(); } catch (err) { $("acctErr").textContent = err.message; } });
      del.addEventListener("click", async () => { try { $("acctErr").textContent = ""; await api("DELETE", "/api/accounts/" + a.id); await load(); renderAccts(); } catch (err) { $("acctErr").textContent = err.message; } });
      li.append(inp, used, save, del); ul.appendChild(li);
    });
    if (!state.accounts.length) { const li = document.createElement("li"); li.className = "muted"; li.textContent = "No accounts yet."; ul.appendChild(li); }
  }
  $("btnAccts").addEventListener("click", () => { $("acctErr").textContent = ""; renderAccts(); openDlg($("acctDlg")); $("a_new").focus(); });
  $("acctForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { $("acctErr").textContent = ""; await api("POST", "/api/accounts", { name: $("a_new").value }); $("a_new").value = ""; await load(); renderAccts(); }
    catch (err) { $("acctErr").textContent = err.message; }
  });

  // ---------- import (a .json file, or a .csv in the same columns as Export CSV)
  function parseCSV(text) {
    const rows = []; let row = [], f = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
      else if (c === '"') q = true;
      else if (c === ",") { row.push(f); f = ""; }
      else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(f); f = ""; if (row.some((x) => x !== "")) rows.push(row); row = []; }
      else f += c;
    }
    row.push(f); if (row.some((x) => x !== "")) rows.push(row);
    return rows;
  }
  function anyDate(s) {   // accepts 2026-11-22 or 11/22/2026
    s = (s || "").trim(); if (!s) return "";
    const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    return m ? m[3] + "-" + pad(m[1]) + "-" + pad(m[2]) : s;
  }
  function csvToBills(text) {
    const rows = parseCSV(text.replace(/^﻿/, "")); if (rows.length < 2) throw new Error("The file has no rows");
    const h = rows[0].map((x) => x.trim().toLowerCase());
    const col = (...names) => { for (const n of names) { const i = h.indexOf(n); if (i >= 0) return i; } return -1; };
    const ix = { name: col("bill name", "name"), acct: col("from account", "account"), amt: col("amount due"), due: col("date due", "due date"),
      fv: col("freq value", "frequency"), fu: col("freq unit"), lpd: col("date paid"), lpa: col("paid previously"),
      tr: col("track remaining"), bal: col("amount left", "balance") };
    if (ix.name < 0 || ix.due < 0) throw new Error("The CSV needs at least Bill Name and Date Due columns");
    const get = (r, i) => (i >= 0 && r[i] !== undefined ? r[i].trim() : "");
    return rows.slice(1).map((r) => ({
      name: get(r, ix.name), account: get(r, ix.acct), amount_due: get(r, ix.amt) || "0", due_date: anyDate(get(r, ix.due)),
      freq_value: get(r, ix.fv) || "1", freq_unit: (get(r, ix.fu) || "month").toLowerCase(),
      last_paid_date: anyDate(get(r, ix.lpd)), last_paid_amount: get(r, ix.lpa),
      track_balance: ["yes", "true", "1"].includes(get(r, ix.tr).toLowerCase()), balance: get(r, ix.bal),
    }));
  }
  $("btnImport").addEventListener("click", () => {
    $("impErr").textContent = ""; $("impFile").value = ""; $("impPreview").hidden = true; $("impGo").disabled = true; state.imp = null;
    openDlg($("impDlg"));
  });
  $("impFile").addEventListener("change", async () => {
    const file = $("impFile").files[0]; if (!file) return;
    $("impErr").textContent = ""; $("impPreview").hidden = true; $("impGo").disabled = true; state.imp = null;
    try {
      const text = await file.text();
      let bills;
      if (/\.csv$/i.test(file.name)) bills = csvToBills(text);
      else { const j = JSON.parse(text); bills = Array.isArray(j) ? j : j.bills; }
      if (!Array.isArray(bills) || !bills.length) throw new Error("The file has no bills");
      state.imp = bills;
      const have = new Set(state.bills.map((b) => b.name.toLowerCase()));
      const dup = bills.filter((b) => have.has(String(b.name || "").trim().toLowerCase())).length;
      $("impSummary").textContent = bills.length + " bills in the file" + (dup ? "; " + dup + " already exist and will be skipped." : ".") + " Check them, then press Import.";
      const body = $("impBody"); body.replaceChildren();
      bills.forEach((b) => { const tr = document.createElement("tr"); cell(tr, b.name); cell(tr, b.account || ""); cell(tr, usd(String(b.amount_due || "0").replace(/[$,]/g, "")), "n"); cell(tr, mdy(b.due_date)); cell(tr, unitText(b.freq_value || 1, b.freq_unit || "month")); body.appendChild(tr); });
      $("impPreview").hidden = false; $("impGo").disabled = false;
    } catch (err) { $("impErr").textContent = err instanceof SyntaxError ? "That is not a valid JSON file." : err.message; }
  });
  $("impGo").addEventListener("click", async () => {
    if (!state.imp) return;
    $("impGo").disabled = true;
    try {
      const r = await api("POST", "/api/import", { bills: state.imp });
      $("impDlg").close(); await load();
      alert("Imported " + r.added + " bill" + (r.added === 1 ? "" : "s") + (r.skipped ? " (" + r.skipped + " skipped as duplicates)." : "."));
    } catch (err) { $("impErr").textContent = err.message; $("impGo").disabled = false; }
  });

  // The button bar is pinned to the top; tell the CSS how tall it is so the column headings stick right below it.
  const bar = document.querySelector(".toolbar");
  const setBar = () => document.documentElement.style.setProperty("--bar-h", Math.round(bar.getBoundingClientRect().height) + "px");
  setBar(); window.addEventListener("resize", setBar);
  if (window.ResizeObserver) new ResizeObserver(setBar).observe(bar);

  load().catch((err) => { $("count").textContent = err.message; });
})();
