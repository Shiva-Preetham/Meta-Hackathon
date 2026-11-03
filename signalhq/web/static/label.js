// Keyboard-first labelling: 1-9 picks the type, a-m the product area, "/" focuses the
// theme box, Enter saves and loads the next item. Built for ~150 labels in ~45 minutes.
"use strict";

const $ = (id) => document.getElementById(id);
const AREA_KEYS = "abcdefghijklm";
const s = { item: null, type: null, area: null, types: [], areas: [] };

function choice(label, key, onPick, selected) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `choice${selected ? " on" : ""}`;
  const k = document.createElement("kbd");
  k.textContent = key;
  b.append(k, document.createTextNode(label.replace(/_/g, " ")));
  b.addEventListener("click", onPick);
  return b;
}

function draw() {
  $("types").replaceChildren(...s.types.map((t, i) => choice(t, String(i + 1), () => { s.type = t; draw(); }, s.type === t)));
  $("areas").replaceChildren(...s.areas.map((a, i) => choice(a, AREA_KEYS[i], () => { s.area = a; draw(); }, s.area === a)));
  $("save").disabled = !(s.item && s.type && s.area);
}

async function loadThemes() {
  const names = await (await fetch("/api/labels/themes")).json();
  $("theme-list").replaceChildren(...names.map((n) => { const o = document.createElement("option"); o.value = n; return o; }));
}

async function next() {
  const data = await (await fetch("/api/labels/next")).json();
  s.types = data.types; s.areas = data.areas; s.item = data.item; s.type = null; s.area = null;
  $("count").textContent = `${data.done} of ${data.total} labelled`;
  $("bar").style.width = data.total ? `${(100 * data.done) / data.total}%` : "0";
  if (!data.total) {
    $("text").textContent = "No sample yet. Run: python -m signalhq eval sample";
  } else if (!data.item) {
    $("text").textContent = "All items are labelled. Run: python -m signalhq eval report";
  } else {
    $("text").textContent = data.item.text;
    $("source").textContent = `Item ${data.item.item_id} · ${data.item.source}`;
  }
  $("theme").value = "";
  draw();
}

async function save() {
  const theme = $("theme").value.trim();
  if (!(s.item && s.type && s.area)) { $("status").textContent = "Pick a type and a product area first."; return; }
  if (!theme) { $("status").textContent = "Add a short theme label."; $("theme").focus(); return; }
  const res = await fetch("/api/labels", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ item_id: s.item.item_id, type: s.type, product_area: s.area, theme_label: theme }),
  });
  $("status").textContent = res.ok ? `Saved item ${s.item.item_id}.` : `Save failed: HTTP ${res.status}`;
  if (res.ok) { $("theme").blur(); await loadThemes(); await next(); }
}

document.addEventListener("keydown", (e) => {
  const typing = document.activeElement === $("theme");
  if (e.key === "Enter") { e.preventDefault(); save(); return; }
  if (typing) { if (e.key === "Escape") $("theme").blur(); return; }
  if (e.key === "/") { e.preventDefault(); $("theme").focus(); return; }
  const n = Number(e.key);
  if (n >= 1 && n <= s.types.length) { s.type = s.types[n - 1]; draw(); return; }
  const a = AREA_KEYS.indexOf(e.key.toLowerCase());
  if (a >= 0 && a < s.areas.length) { s.area = s.areas[a]; draw(); }
});
$("save").addEventListener("click", save);

loadThemes();
next();
