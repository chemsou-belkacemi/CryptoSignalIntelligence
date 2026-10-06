"use strict";
// Tableau de bord interactif de CSI. Analyse seulement : aucun ordre, rien n'est envoyé à BinanceSpotManager.
// Tout texte reçu est inséré avec textContent, jamais comme du HTML.

const TOKEN_KEY = "csi_api_token";
const PREFS_KEY = "csi_dashboard_prefs";
const PARIS = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" });
const state = { horizon: "24h", pairs: [], horizons: [], followLoaded: false, marketRun: 0, opportunitiesRun: 0 };
const SVG = "http://www.w3.org/2000/svg";
const COPY_LABEL = "Copier le résumé (pas un signal)";

// États descriptifs : jamais une proposition d'entrer (aucun n'est affiché en vert).
const PLAN_STATES = {
  HISTORIQUE_POSITIF_NON_VALIDE: ["warn", "Historique positif — non validé", "Dans des conditions comparables, ce plan a gagné en moyenne, au-delà de la simple dérive passée. Statistique en échantillon, non validée par le protocole : ce n'est PAS une proposition d'entrer."],
  AUCUN_AVANTAGE_HISTORIQUE: ["neutral", "Aucun avantage historique", "Pas d'avantage démontré dans ces conditions (intervalle corrigé contenant 0, ou simple dérive passée) : CSI ne propose pas d'entrer."],
  HISTORIQUE_DEFAVORABLE: ["bad", "Historique défavorable", "Dans des conditions comparables, ce plan a perdu en moyenne : CSI ne propose pas d'entrer."],
  INSUFFISANT: ["neutral", "Historique insuffisant", "Pas assez de blocs de jours indépendants (ou données récentes inutilisables) pour juger ce plan."],
  FAVORABLE_PROUVE_EN_DIRECT: ["ok", "Favorable — prouvé en direct", "Les plans de ce type (même horizon, historique positif) ont gagné EN DIRECT, sur des données que personne n'avait vues au moment du plan : au moins 50 plans sur 20 jours, intervalle de confiance entièrement positif. Ce n'est pas une garantie pour CE plan."],
  DONNEES_ANCIENNES: ["bad", "Données anciennes", "La dernière bougie est trop vieille : la surveillance de CSI tourne-t-elle ? Analyse non exploitable."],
};
const SIGNAL_VERDICTS = {
  FAVORABLE: ["ok", "Favorable"], INDETERMINE: ["warn", "Indéterminé"], DEFAVORABLE: ["bad", "Défavorable"],
  REFUSE: ["bad", "Refusé"], EN_ATTENTE: ["neutral", "En attente"],
};
const REGIMES = {
  BULL: "haussière", BEAR: "baissière", NEUTRAL: "neutre", RANGE: "sans tendance", HIGH: "haute", LOW: "basse",
  NORMAL: "normale", UNKNOWN: "inconnue",
};

// --- utilitaires ------------------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const fmt = (v, digits = 2, signed = false) =>
  isNum(v) ? (signed && v > 0 ? "+" : "") + v.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "–";
const price = (v) => {
  const n = typeof v === "string" ? Number(v) : v;
  if (!isNum(n)) return "–";
  const digits = n >= 1000 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 6 : 8;
  return n.toLocaleString("fr-FR", { minimumFractionDigits: Math.min(digits, 2), maximumFractionDigits: digits });
};
const pctFrac = (v, digits = 1, signed = false) => (isNum(v) ? fmt(v * 100, digits, signed) + " %" : "–");
const pctUnits = (v, digits = 2, signed = true) => (isNum(v) ? fmt(v, digits, signed) + " %" : "–");
const ciFrac = (ci, digits = 1) => (Array.isArray(ci) ? `[${pctFrac(ci[0], digits)} ; ${pctFrac(ci[1], digits)}]` : "(intervalle non fiable)");
const ciR = (ci) => (Array.isArray(ci) ? `[${fmt(ci[0], 2, true)} ; ${fmt(ci[1], 2, true)}] R` : "(intervalle non fiable)");
// Couleur seulement si l'intervalle exclut 0 ; sinon neutre (même si la moyenne est positive).
const ciClass = (ci) => (Array.isArray(ci) ? (ci[0] > 0 ? "warn" : ci[1] < 0 ? "bad" : "muted") : "muted");
const regime = (v) => REGIMES[v] || (v ? String(v).toLowerCase() : "–");
const pair = (s) => (s && /USD[TC]$/.test(s) ? `${s.slice(0, -4)}/${s.slice(-4)}` : s);

function when(iso) {
  if (!iso) return "–";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso) : PARIS.format(date) + " (Paris)";
}

function age(minutes) {
  if (!isNum(minutes)) return "inconnue";
  if (minutes < 60) return `${Math.max(0, Math.round(minutes))} min`;
  if (minutes < 2880) return `${Math.floor(minutes / 60)} h ${String(Math.round(minutes % 60)).padStart(2, "0")}`;
  return `${Math.round(minutes / 1440)} j`;
}

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {}; } catch (_err) { return {}; }
}
function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(Object.assign(loadPrefs(), prefs))); } catch (_err) { /* stockage indisponible */ }
}
function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch (_err) { return null; }
}

async function api(path, body) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  const options = { method: body ? "POST" : "GET", headers };
  if (body) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch (_err) { data = null; }
  if (response.status === 401) {
    document.getElementById("token-box").classList.remove("hidden");
    throw new Error("jeton d'accès requis (voir en haut de la page)");
  }
  if (!response.ok) throw new Error((data && data.error) || `erreur ${response.status}`);
  return data;
}

function card(title, ...children) {
  return el("section", { class: "card" }, el("h2", { text: title }), ...children);
}

function kv(rows) {
  return el("dl", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v, cls]) => [el("dt", { text: k }), el("dd", { class: cls || "" }, v)]));
}

function table(headers, rows, empty) {
  if (!rows.length) return el("p", { class: "muted", text: empty });
  return el("table", {},
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { class: h.num ? "num" : "", text: h.label || h })))),
    el("tbody", {}, rows.map((cells) => el("tr", { class: cells.rowClass || "" },
      cells.map((c, i) => el("td", { class: headers[i] && headers[i].num ? "num" : (c && c.cls) || "" }, c && c.node ? c.node : c))))));
}

function banner(kind, title, text, extra) {
  return el("div", { class: `banner ${kind}` }, el("strong", { class: kind === "neutral" ? "" : kind, text: title }), el("span", { text }), extra || null);
}

function showError(target, error) {
  target.replaceChildren(el("section", { class: "card" }, el("p", { class: "error", text: "Erreur : " + error.message })));
}

function busy(target, text) {
  target.replaceChildren(el("p", { class: "spinner", text }));
}

async function copy(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copié";
  } catch (_err) {
    button.textContent = "Copie impossible : sélectionner le texte";
  }
  setTimeout(() => { button.textContent = COPY_LABEL; }, 2500);
}

// --- en-tête : santé de la surveillance ------------------------------------------------------------
async function refreshHealth() {
  const pill = document.getElementById("health");
  try {
    const h = await api("/health");
    const [cls, text] = h.ready && !h.degraded ? ["ok", "surveillance : à jour"]
      : h.ready ? ["warn", "surveillance : dégradée"] : ["bad", "surveillance : arrêtée ou pas prête"];
    pill.className = `pill ${cls}`;
    pill.textContent = text;
    pill.title = h.detail || "";
  } catch (error) {
    pill.className = "pill bad";
    pill.textContent = "API injoignable";
    pill.title = error.message;
  }
}

// --- onglet Paire -----------------------------------------------------------------------------------
async function loadPairs() {
  const select = document.getElementById("pair");
  const segmented = document.getElementById("horizons");
  const prefs = loadPrefs();
  const data = await api("/pairs");
  state.pairs = data.pairs;
  select.replaceChildren(...data.pairs.map((p) => el("option", { value: p.symbol, text: `${pair(p.symbol)} — données il y a ${age(p.age_minutes)}` })));
  if (prefs.symbol && data.pairs.some((p) => p.symbol === prefs.symbol)) select.value = prefs.symbol;
  state.horizon = data.horizons.some((h) => h.key === prefs.horizon) ? prefs.horizon : "24h";
  state.horizons = data.horizons;
  renderHorizonButtons(document.getElementById("market-horizons"));
  renderHorizonButtons(segmented);
}

async function analyzePair() {
  const button = document.getElementById("analyze");
  const target = document.getElementById("pair-result");
  const symbol = document.getElementById("pair").value;
  if (!symbol) return;
  savePrefs({ symbol, horizon: state.horizon });
  button.disabled = true;
  busy(target, `Analyse de ${pair(symbol)} en cours (quelques secondes : tout l'historique de la paire est relu)…`);
  try {
    renderPair(await api("/analyze-pair", { symbol, horizon: state.horizon }));
  } catch (error) {
    showError(target, error);
  } finally {
    button.disabled = false;
  }
}

async function refreshPair(symbol, button) {
  button.disabled = true;
  button.textContent = "Téléchargement des bougies manquantes…";
  try {
    await api("/refresh-pair", { symbol });
    await loadPairs();
    document.getElementById("pair").value = symbol;
    await analyzePair();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Réessayer : " + error.message;
  }
}

function renderPair(r) {
  const target = document.getElementById("pair-result");
  const plan = r.plan || {};
  const [kind, title, text] = PLAN_STATES[plan.state] || ["neutral", plan.state || "?", ""];
  const head = banner(kind, `${pair(r.symbol)} · ${r.horizon_label} · ${title}`, text,
    el("div", { class: "small muted", text: r.warning }));
  const futures = el("div");
  const forecast = el("div");
  const moves = el("div");
  target.replaceChildren(head, el("div", { class: "grid" }, contextCard(r), planCard(r)), moves, futures, overviewCard(r),
    strategiesCard(r), forecast, definitionsCard(r));
  volatilityCard(r.symbol).then((c) => moves.replaceChildren(c), () => moves.replaceChildren());
  futures.replaceChildren(card("Marché à terme — positionnement du moment", el("p", { class: "muted small", text: "Lecture des données publiques du marché à terme…" })));
  derivativesCard(r.symbol).then((c) => futures.replaceChildren(c), (error) => futures.replaceChildren(
    card("Marché à terme — positionnement du moment", el("p", { class: "muted small", text: `Indisponible : ${error.message}` }))));
  forecastCard().then((c) => forecast.replaceChildren(c), () => forecast.replaceChildren());
}

// Volatilité prévue : l'AMPLEUR attendue à 1, 3 et 7 jours (modèles retenus par le protocole), jamais le sens.
const VOL_HORIZONS = [["1", "1 jour"], ["3", "3 jours"], ["7", "7 jours"]];
const moveText = (h) => (h && isNum(h.move_pct) ? `±${fmt(h.move_pct, 2)} %` : "–");
const calmText = (h) => {
  if (!h || !isNum(h.ratio)) return "";
  if (h.ratio >= 1.15) return `plus agité que ces 7 derniers jours (×${fmt(h.ratio, 2)})`;
  if (h.ratio <= 0.87) return `plus calme que ces 7 derniers jours (×${fmt(h.ratio, 2)})`;
  return "comme ces 7 derniers jours";
};

async function volatilityCard(symbol) {
  const d = await api(`/volatility?symbol=${encodeURIComponent(symbol)}`);
  const title = "Ampleur attendue — prévision de volatilité";
  if (!d.available) return card(title, el("p", { class: "muted small", text: d.reason || "indisponible" }));
  const f = d.forecast || {};
  if (!f.available) return card(title, el("p", { class: "muted small", text: `Pas de prévision pour cette paire : ${f.reason || "indisponible"}.` }));
  return card(title,
    table(["Horizon", { label: "Mouvement typique attendu", num: true }, { label: "Ces 7 derniers jours", num: true }, "Lecture"],
      VOL_HORIZONS.map(([key, label]) => { const h = f.horizons[key]; return [label, moveText(h), h && isNum(h.recent_move_pct) ? `±${fmt(h.recent_move_pct, 2)} %` : "–", calmText(h)]; }), "aucune prévision"),
    el("p", { class: "muted small", text: `${d.note} Prévision du ${when(d.origin)}, refaite chaque jour.` }));
}

async function loadVolatility() {
  const target = document.getElementById("volatility-result");
  if (!target || state.volatilityLoaded) return;
  busy(target, "Prévisions du jour…");
  try {
    const d = await api("/volatility");
    state.volatilityLoaded = true;
    if (!d.available) { target.replaceChildren(card("Prévisions du jour — ampleur attendue", el("p", { class: "muted", text: d.reason }))); return; }
    const rows = Object.entries(d.pairs || {}).filter(([, f]) => f.available)
      .sort((a, b) => ((b[1].horizons["1"] || {}).move_pct || 0) - ((a[1].horizons["1"] || {}).move_pct || 0))
      .map(([symbol, f]) => [{ node: el("strong", { text: pair(symbol) }) }, ...VOL_HORIZONS.map(([key]) => moveText(f.horizons[key])), calmText(f.horizons["1"])]);
    const missing = Object.values(d.pairs || {}).filter((f) => !f.available).length;
    target.replaceChildren(card(`Prévisions du jour — ampleur attendue (${rows.length} paires)`,
      el("p", { class: "muted small", text: `${d.note} Prévision du ${when(d.origin)}.` + (missing ? ` ${missing} paire(s) sans prévision (historique trop court ou données manquantes).` : "")
        + " Non confirmées sur la période finale réservée (VOLATILITY.md § 19) : affichage seulement, rien n'en dépend." }),
      table(["Paire", { label: "1 jour", num: true }, { label: "3 jours", num: true }, { label: "7 jours", num: true }, "Demain par rapport à ces 7 derniers jours"], rows, "aucune prévision")));
  } catch (error) {
    showError(target, error);
  }
}

// Risque à 24 h (shadow) : seule prévision de volatilité confirmée hors échantillon (VOLATILITY.md § 19).
// --- analyse technique d'une paire (technical/analysis.py) : lecture mécanique du graphique ----------------------
const TECH_TF = [["1h", "1 h"], ["4h", "4 h"], ["1d", "1 jour"]];
const FIGURE_NAMES = { HEAD_SHOULDERS: "tête-épaules", DOUBLE: "double creux/sommet", FLAG: "drapeau", CUP_HANDLE: "coupe avec anse",
  TRIANGLE: "triangle / biseau", TRENDLINE: "ligne de tendance", ABCD: "ABCD", ICT: "ICT/SMC", GARTLEY: "Gartley", BAT: "Bat",
  BUTTERFLY: "Butterfly", CRAB: "Crab" };

function svgEl(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}

function technicalChart(d) {
  const W = 960, H = 380, L = 8, R = 92, T = 10, B = 22;
  const bars = d.bars || [];
  if (!bars.length) return null;
  const close = d.close;
  const near = (p) => isNum(p) && Math.abs(p / close - 1) < 0.25;
  const plan = d.plan || {};
  const extra = [...(d.resistances || []).slice(0, 3), ...(d.supports || []).slice(0, 3)].map((x) => x.price)
    .concat(plan.state === "PLAN" ? [plan.stop, ...(plan.targets || []).map((t) => t.price)] : []).filter(near);
  let lo = Math.min(...bars.map((b) => b[3]), ...extra), hi = Math.max(...bars.map((b) => b[2]), ...extra);
  const pad = (hi - lo) * 0.04 || hi * 0.01; lo -= pad; hi += pad;
  const y = (p) => T + ((hi - p) / (hi - lo)) * (H - T - B);
  const step = (W - L - R) / bars.length;
  const x = (i) => L + (i + 0.5) * step;
  const index = new Map(bars.map((b, i) => [b[0], i]));
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "ta-chart", role: "img",
    "aria-label": `Graphique ${d.symbol} ${d.timeframe}` });
  const hline = (p, cls, label) => {
    if (!(p > lo && p < hi)) return;
    svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(p), y2: y(p), class: cls }));
    svg.append(svgEl("text", { x: W - R + 4, y: y(p) + 4, class: `ta-label ${cls}` }, label));
  };
  for (const z of d.zones || []) {
    const i = index.has(z.from) ? index.get(z.from) : 0;
    const top = Math.min(hi, z.top), bottom = Math.max(lo, z.bottom);
    if (top <= bottom) continue;
    svg.append(svgEl("rect", { x: x(i) - step / 2, y: y(top), width: W - R - (x(i) - step / 2), height: y(bottom) - y(top),
      class: `ta-zone ${z.side === "bull" ? "bull" : "bear"}` }));
  }
  bars.forEach((b, i) => {
    const up = b[4] >= b[1];
    svg.append(svgEl("line", { x1: x(i), x2: x(i), y1: y(b[2]), y2: y(b[3]), class: `ta-wick ${up ? "up" : "down"}` }));
    const top = y(Math.max(b[1], b[4])), h = Math.max(1, Math.abs(y(b[1]) - y(b[4])));
    svg.append(svgEl("rect", { x: x(i) - step * 0.35, y: top, width: step * 0.7, height: h, class: `ta-body ${up ? "up" : "down"}` }));
  });
  (d.resistances || []).slice(0, 3).forEach((r, k) => hline(r.price, "ta-res", `R${k + 1} ${price(r.price)}`));
  (d.supports || []).slice(0, 3).forEach((s, k) => hline(s.price, "ta-sup", `S${k + 1} ${price(s.price)}`));
  const prev = d.previous || {};
  for (const [key, label] of [["PDH", "veille haut"], ["PDL", "veille bas"], ["PWH", "sem. haut"], ["PWL", "sem. bas"]]) {
    if (isNum(prev[key])) hline(prev[key], "ta-prev", label);
  }
  for (const f of d.figures || []) {
    const pts = (f.points || []).filter((p) => index.has(p.time) && p.price > lo && p.price < hi);
    if (pts.length < 2) continue;
    svg.append(svgEl("polyline", { points: pts.map((p) => `${x(index.get(p.time))},${y(p.price)}`).join(" "),
      class: `ta-fig ${f.side === "bull" ? "bull" : "bear"}` }));
  }
  if (plan.state === "PLAN") {
    hline(plan.stop, "ta-stop", `stop ${price(plan.stop)}`);
    (plan.targets || []).forEach((t, k) => hline(t.price, "ta-tp", `TP${k + 1} ${price(t.price)}`));
  }
  hline(close, "ta-last", `${price(close)}`);
  svg.append(svgEl("text", { x: L, y: H - 6, class: "ta-axis" }, (bars[0][0] || "").slice(0, 16).replace("T", " ")));
  svg.append(svgEl("text", { x: W - R, y: H - 6, class: "ta-axis", "text-anchor": "end" }, (bars[bars.length - 1][0] || "").slice(0, 16).replace("T", " ")));
  return svg;
}

function technicalBody(d) {
  const plan = d.plan || {};
  const dist = (p) => `${fmt((p / d.close - 1) * 100, 2, true)} %`;
  const levelRows = (rows) => rows.map((r) => [price(r.price), dist(r.price), r.touches, when(r.last_touch)]);
  const planNode = plan.state === "PLAN"
    ? el("div", {}, kv([["Entrée (dernière clôture)", price(plan.entry)], ["Stop (sous le support)", `${price(plan.stop)} (−${fmt(plan.stop_pct, 2)} %)`],
        ...(plan.targets || []).map((t, k) => [`TP${k + 1} (résistance)`, `${price(t.price)} (+${fmt(t.pct, 2)} % · ${fmt(t.r, 2)} R)`])]),
      plan.note ? el("p", { class: "warn small", text: plan.note }) : null)
    : el("p", { class: "muted", text: `Pas de plan d'achat : ${plan.reason}` });
  return el("div", {},
    el("ul", { class: "ta-summary" }, (d.summary || []).map((line) => el("li", { text: line }))),
    el("h3", { text: "Plan indicatif (long seulement)" }), planNode,
    el("div", { class: "grid" },
      el("div", {}, el("h3", { text: "Résistances" }), table(["Prix", "Distance", { label: "Touches", num: true }, "Dernière"], levelRows(d.resistances || []), "aucune au-dessus")),
      el("div", {}, el("h3", { text: "Supports" }), table(["Prix", "Distance", { label: "Touches", num: true }, "Dernière"], levelRows(d.supports || []), "aucun en dessous"))),
    el("h3", { text: "Figures récentes (20 dernières bougies)" }),
    table(["Figure", "Sens", "Détectée", "Entrée · stop · objectifs"], (d.figures || []).map((f) => [FIGURE_NAMES[f.family] || f.family,
      f.side === "bull" ? "haussière" : "baissière", when(f.detected_at),
      f.side === "bull" && isNum(f.entry) ? `${price(f.entry)} · ${price(f.stop)} · ${(f.targets || []).map(price).join(" / ")}` : "—"]), "aucune"),
    el("p", { class: "muted small", text: `${d.warning} Prix arrondis au pas de cotation ${d.tick || "(inconnu : non arrondis)"}. ` +
      "Légende : rouge = résistance, vert = support, pointillés gris = plus haut et plus bas de la veille et de la semaine, " +
      "zones = FVG et order blocks encore actifs, traits orange = figures, bleu = plan." }));
}

async function loadTechnical(force = false) {
  const target = document.getElementById("technical-result");
  if (!target || (state.technicalLoaded && !force)) return;
  state.technicalLoaded = true;
  state.technical = state.technical || { symbol: "BTCUSDT", timeframe: "4h" };
  let pairs = [];
  try { pairs = (await api("/analysis/pairs")).pairs || []; } catch (error) { showError(target, error); return; }
  if (!pairs.includes(state.technical.symbol) && pairs.length) state.technical.symbol = pairs[0];
  const select = el("select", { "aria-label": "Paire" }, pairs.map((p) => el("option", { value: p, text: pair(p) })));
  select.value = state.technical.symbol;
  const tfs = el("div", { class: "segmented" });
  const out = el("div", {});
  const run = async () => {
    state.technical.symbol = select.value;
    for (const b of tfs.children) b.classList.toggle("on", b.dataset.key === state.technical.timeframe);
    busy(out, "Analyse…");
    try {
      const d = await api(`/analysis?symbol=${encodeURIComponent(state.technical.symbol)}&timeframe=${state.technical.timeframe}`);
      out.replaceChildren(el("div", { class: "ta-wrap" }, technicalChart(d)), technicalBody(d));
    } catch (error) {
      showError(out, error);
    }
  };
  for (const [key, label] of TECH_TF) {
    tfs.append(el("button", { type: "button", "data-key": key, text: label,
      onclick: () => { state.technical.timeframe = key; run(); } }));
  }
  select.addEventListener("change", run);
  target.replaceChildren(card("Analyse technique d'une paire (lecture mécanique du graphique)",
    el("div", { class: "row wrap" }, el("div", { class: "field" }, "Paire", select), el("div", { class: "field" }, "Unité de temps", tfs)),
    out));
  run();
}

async function loadRisk() {
  const target = document.getElementById("risk-result");
  if (!target || state.riskLoaded) return;
  busy(target, "Risque à 24 h…");
  try {
    const d = await api("/risk");
    state.riskLoaded = true;
    if (!d.available) { target.replaceChildren(card("Risque à 24 h (shadow)", el("p", { class: "muted", text: d.reason }))); return; }
    const rows = Object.entries(d.pairs || {}).map(([symbol, r]) => [{ node: el("strong", { text: pair(symbol) }) },
      `±${fmt(r.move_24h_pct, 2)} %`, `× ${fmt(r.relative_size, 2)}`]);
    target.replaceChildren(card(`Risque à 24 h — prévision confirmée, en shadow (${rows.length} paires)`,
      el("p", { class: "muted small", text: `${d.note} Prévision du ${when(d.origin)} ; ampleur médiane ${fmt(d.median_move_24h_pct, 2)} %.` }),
      table(["Paire", { label: "Ampleur typique 24 h", num: true }, { label: "Taille relative (risque égal)", num: true }], rows, "aucune paire")));
  } catch (error) {
    showError(target, error);
  }
}

// Signaux Telegram reçus en IMAGE à valider (phase 3) : jamais simulés sans la validation du propriétaire.
const IMAGE_STATUS = { RECUE: "à lire", SUR: "sûres", A_VALIDER: "à valider", IGNOREE: "ignorées", VALIDEE: "validées", REFUSEE: "refusées" };

async function loadImages() {
  const target = document.getElementById("images-result");
  if (!target) return;
  busy(target, "Signaux en image…");
  try {
    const d = await api("/images/pending");
    const counts = Object.entries(d.counts || {}).map(([k, v]) => `${v} ${IMAGE_STATUS[k] || k}`).join(" · ") || "aucune image reçue";
    const items = (d.pending || []).map((item) => {
      const text = el("textarea", { rows: 7, spellcheck: "false" });
      text.value = item.ocr_text || "";
      const decide = async (accept) => {
        try {
          await api("/images/decide", { id: item.id, accept, text: accept ? text.value : undefined });
          loadImages();
        } catch (error) { showError(target, error); }
      };
      return el("div", { class: "card" },
        el("p", { class: "muted small", text: `Reçue ${when(item.received_at)} · conversation ${item.chat} · légende : ${item.caption || "aucune"}` }),
        item.image ? el("img", { src: item.image, alt: "capture du signal", class: "signal-image" }) : el("p", { text: "image absente" }),
        el("p", { class: "small", text: "Pourquoi à valider : " + (item.ocr_notes || []).join(" ; ") }),
        el("label", { class: "field block" }, "Niveaux lus (corrigez si besoin, puis validez)", text),
        el("div", { class: "row wrap" },
          el("button", { class: "primary", text: "Valider", onclick: () => decide(true) }),
          el("button", { text: "Refuser", onclick: () => decide(false) })));
    });
    target.replaceChildren(card(`Signaux en image à valider (${items.length})`,
      el("p", { class: "muted small", text: `Images reçues par le 2e bot : ${counts}. À valider dans les ${d.validation_delay_hours || 2} h qui suivent la lecture (sinon expirée). Une image validée est jouée à l'heure de la validation, jamais avant ; la paire ne peut pas changer ; une image refusée ne l'est jamais. Information : aucun ordre.` }),
      ...(items.length ? items : [el("p", { class: "muted", text: "Rien à valider." })])));
  } catch (error) {
    showError(target, error);
  }
}

// Marché à terme : données PUBLIQUES de positionnement, information seulement (aucune décision, aucun contrat).
const usd = (v) => (isNum(v) ? new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 2 }).format(v) + " $" : "–");
const rankText = (r, days) => (isNum(r) ? `rang ${pctFrac(r, 0)} sur ${fmt(days, 0)} j` : "rang –");
const DERIVATIVE_LABELS = { financement: "Financement", prime: "Prime", interet_ouvert: "Intérêt ouvert", comptes: "Comptes",
  gros_comptes: "Gros comptes", agressifs: "Achats / ventes agressifs", rang: "Rang" };

async function derivativesCard(symbol) {
  const d = await api(`/derivatives?symbol=${encodeURIComponent(symbol)}`);
  const off = (s) => !s || s.unavailable;
  const why = (s) => (s && s.unavailable) || "indisponible";
  const f = d.funding, p = d.premium, oi = d.open_interest, a = d.accounts, t = d.top_positions, k = d.taker;
  return card(`Marché à terme — positionnement du moment (${pair(symbol)})`,
    el("p", { class: "muted small", text: d.note }),
    kv([
      ["Financement, dernier règlement", off(f) ? why(f) : `${pctFrac(f.last_rate, 4, true)} par ${f.interval_hours} h (${when(f.last_at)})`],
      ["Financement estimé, prochain règlement", off(f) ? "–" : `${pctFrac(f.estimated_next_rate, 4, true)} à ${when(f.next_at)}`],
      ["Financement moyen sur 3 jours", off(f) ? "–" : `${pctFrac(f.mean_3d, 4, true)} (≈ ${pctFrac(f.annualized_3d, 1, true)} par an) · ${rankText(f.rank_mean_3d, f.window_days)}`],
      ["Prime du perpétuel sur le Spot", off(p) ? why(p) : `${pctFrac(p.now, 3, true)} maintenant · moyenne 24 h ${pctFrac(p.mean_24h, 3, true)} · ${rankText(p.rank_mean_24h, p.window_days)}`],
      ["Intérêt ouvert", off(oi) ? why(oi) : `${usd(oi.value_usd)} · 24 h ${pctFrac(oi.change_24h, 1, true)} · 7 j ${pctFrac(oi.change_7d, 1, true)} · variation 24 h : ${rankText(oi.rank_change_24h, oi.window_days)}`],
      ["Comptes acheteurs / vendeurs", off(a) ? why(a) : `${fmt(a.ratio, 2)} (${pctFrac(a.long_share, 0)} de comptes acheteurs) · ${rankText(a.rank, a.window_days)}`],
      ["Gros comptes : positions acheteuses / vendeuses", off(t) ? why(t) : `${fmt(t.ratio, 2)} (${pctFrac(t.long_share, 0)} acheteuses) · ${rankText(t.rank, t.window_days)}`],
      ["Achats / ventes agressifs sur 24 h", off(k) ? why(k) : `${fmt(k.ratio_24h, 2)} · ${rankText(k.rank, k.window_days)}`],
    ]),
    el("details", {}, el("summary", { text: "Ce que veut dire chaque ligne" }),
      kv(Object.entries(d.definitions || {}).map(([key, text]) => [DERIVATIVE_LABELS[key] || key, text]))),
    el("p", { class: "muted small", text: `Source : ${d.source}${d.cached ? " (lu il y a moins de 5 min)" : ""}.` }));
}

// CSI n'affiche une prévision que d'un modèle VALIDÉ hors échantillon par son protocole ; aucun ne l'est à ce jour.
async function forecastCard() {
  if (!state.models) state.models = await api("/models");
  const rows = (state.models.models || []).filter((m) => /^ML_(INTRADAY|SWING)_/.test(m.kind));
  const validated = rows.some((m) => verdictClass(m.verdict) === "ok");
  return card("Prévision par modèle",
    el("p", { class: validated ? "warn" : "muted", text: validated
      ? "Un programme ML a un verdict favorable hors échantillon : lire son rapport ; ce tableau de bord n'affiche pas encore ses prévisions."
      : "Aucun modèle de CSI n'est validé hors échantillon à ces horizons : CSI ne donne donc pas de probabilité « prédite ». "
        + "Les fréquences ci-dessus décrivent ce qui s'est passé dans des conditions comparables, pas ce qui va se passer." }),
    table(["Programme", "Verdict du protocole", "Date"], rows.map((m) => [m.label,
      { node: el("span", {}, el("strong", { class: verdictClass(m.verdict), text: m.verdict || m.status }),
        m.detail ? el("div", { class: "small muted", text: m.detail }) : null) }, when(m.created_at)]),
    "aucun programme ML exécuté"));
}

function sparkline(spark) {
  const closes = (spark && spark.closes) || [];
  if (closes.length < 2) return null;
  const lo = Math.min(...closes), hi = Math.max(...closes), span = hi - lo || 1;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 300 60");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("points", closes.map((v, i) => `${(i / (closes.length - 1)) * 300},${58 - ((v - lo) / span) * 56}`).join(" "));
  svg.append(line);
  const change = closes[closes.length - 1] / closes[0] - 1;
  return el("div", {}, svg, el("div", { class: "small muted", text: `7 derniers jours : ${pctFrac(change, 2, true)} (min ${price(lo)}, max ${price(hi)})` }));
}

function contextCard(r) {
  const c = r.context || {};
  return card("Situation actuelle", sparkline(r.spark_7d),
    kv([
      ["Dernier prix (clôture 15 min)", price(c.close)],
      ["Bougie", `${when(c.decision_time)} — il y a ${age(c.data_age_minutes)}`, c.fresh ? "" : "bad"],
      ["Tendance 1 h", regime(c.trend_1h)],
      ["Volatilité 1 h", regime(c.volatility_1h)],
      ["Liquidité 1 h", regime(c.liquidity_1h)],
      ["Variation sur 1 h", pctUnits(c.ret_1h_pct)],
      ["Variation sur 24 h", pctUnits(c.ret_24h_pct)],
      ["BTC sur 24 h", pctUnits(c.btc_ret_24h_pct)],
      ["Écart à la moyenne EMA 50 (15 min)", pctUnits(c.dist_ema50_pct)],
      ["RSI 14 (15 min)", fmt(c.rsi14, 1)],
      ["ATR 14 (15 min)", pctUnits(c.atr_pct, 3, false)],
      ["Volatilité réalisée (échelle 24 h)", pctUnits(c.realized_vol_24h_pct, 2, false)],
    ]),
    c.fresh ? null : el("div", {},
      el("p", { class: "bad small", text: "Données anciennes : la surveillance de CSI (service monitor) ne tourne pas. Mettre à jour cette paire depuis les données publiques de Binance :" }),
      el("button", { type: "button", class: "primary", text: "Mettre à jour les données", onclick: (event) => refreshPair(r.symbol, event.target) })),
    c.context_fresh ? null : el("p", { class: "warn small", text: "Contexte 1 h trop ancien : régime inconnu, statistiques tous régimes." }),
    c.data_gap_recent ? el("p", { class: "warn small", text: "Trou de données récent : indicateurs et plan non fiables." }) : null);
}

function planCard(r) {
  const p = r.plan || {};
  if (p.unavailable) return card(`Plan indicatif — ${r.horizon_label}`, el("p", { class: "muted", text: p.unavailable }));
  const levels = el("div", { class: "levels" },
    el("div", { class: "level" }, el("div", { class: "label", text: "Entrée (au marché, ≈ prix actuel)" }), el("div", { class: "value", text: price(p.entry_reference) })),
    el("div", { class: "level" }, el("div", { class: "label", text: `Stop (${pctUnits(p.stop_pct)})` }), el("div", { class: "value bad", text: price(p.stop) })),
    el("div", { class: "level" }, el("div", { class: "label", text: `Objectif (${pctUnits(p.target_pct)})` }), el("div", { class: "value ok", text: price(p.target) })));
  // Texte produit par CSI, en prose : jamais lisible comme un signal par un bot (testé contre BSM).
  const button = el("button", { type: "button", class: "ghost", text: COPY_LABEL });
  button.addEventListener("click", () => copy(p.copy_text || "", button));
  return card(`Plan indicatif (achat) — horizon ${r.horizon_label}`,
    levels,
    el("h3", { text: `Ce qu'a donné ce plan dans le passé (${p.regime_conditioned ? "même régime" : "tous régimes"}, jusqu'au ${p.history_end})` }),
    kv([
      ["Objectif dépassé avant le stop", pctFrac(p.tp_first)],
      ["Stop touché avant l'objectif", pctFrac(p.sl_first)],
      ["Ni l'un ni l'autre (sortie à l'horizon)", pctFrac(p.timeout)],
      ["Espérance par trade", `${fmt(p.expectancy_r, 2, true)} R ${ciR(p.expectancy_r_ci)}`, ciClass(p.expectancy_r_ci)],
      ["Même plan, tous moments (dérive passée)", `${fmt(p.baseline_expectancy_r, 2, true)} R ${ciR(p.baseline_expectancy_r_ci)}`],
      ["Écart dû aux conditions actuelles", p.regime_conditioned ? `${fmt(p.excess_r, 2, true)} R ${ciR(p.excess_r_ci)}` : "– (régime non utilisé)", ciClass(p.excess_r_ci)],
      ["Gain net moyen par trade", pctUnits(p.mean_net_pct)],
      ["Coûts aller-retour", `${fmt(r.costs_round_trip_pct, 2)} % (${fmt(p.costs_in_r, 2)} R)`],
      ["Année la plus lourde", isNum(p.max_year_share) ? `${pctFrac(p.max_year_share, 0)} du résultat` : "–"],
      ["Moments évalués", `${(p.samples || 0).toLocaleString("fr-FR")} · ${p.blocks || 0} blocs indépendants (${p.sampling_stride_bars > 1 ? `une décision toutes les ${p.sampling_stride_bars} bougies de 15 min` : "chaque bougie de 15 min"})`],
    ]),
    p.live ? el("p", { class: p.live.proven ? "ok" : "muted small", text: `Suivi en direct des plans de ce type (${p.live.label}, état « ${(PLAN_STATES[p.live.state] || [, p.live.state])[1]} ») : `
      + (p.live.resolved ? `${p.live.resolved} terminés, R moyen ${fmt(p.live.r_mean, 2, true)} ${ciR(p.live.ic95)}, ${pctFrac(p.live.win_share)} gagnants` : "aucun encore terminé")
      + ` — ${p.live.progress}.` }) : null,
    el("div", { class: "row" }, button),
    el("p", { class: "muted small", text: "1 R = la perte si le stop est touché. Niveaux arrondis au pas de cotation. Le résumé copié est une phrase, jamais un signal lisible par un bot." }));
}

function overviewCard(r) {
  const rows = r.overview || [];
  const lows = rows.map((o) => o.q10_gross).filter(isNum);
  const highs = rows.map((o) => o.q90_gross).filter(isNum);
  const lo = Math.min(0, ...lows);
  const hi = Math.max(0, ...highs);
  const span = hi - lo || 1;
  const position = (v) => `${((v - lo) / span) * 100}%`;
  const fan = (o) => {
    if (!isNum(o.q10_gross) || !isNum(o.q90_gross)) return "–";
    const bar = el("div", { class: "fan", title: `10 % des cas sous ${pctFrac(o.q10_gross)}, 10 % au-dessus de ${pctFrac(o.q90_gross)}` });
    const range = el("div", { class: "range" });
    range.style.left = position(o.q10_gross);
    range.style.width = `${((o.q90_gross - o.q10_gross) / span) * 100}%`;
    const zero = el("div", { class: "zero" });
    zero.style.left = position(0);
    bar.append(range, zero);
    if (isNum(o.median_gross)) {
      const median = el("div", { class: "median" });
      median.style.left = position(o.median_gross);
      bar.append(median);
    }
    return { node: el("div", {}, bar, el("div", { class: "small muted", text: `${pctFrac(o.q10_gross)} à ${pctFrac(o.q90_gross)}` })) };
  };
  const lines = rows.map((o) => {
    const cells = [
      { node: el("strong", { text: o.label }) },
      { node: el("span", {}, pctFrac(o.p_up.value), el("span", { class: "small muted", text: " " + ciFrac(o.p_up.ci) })) },
      pctFrac(o.p_up_all_moments.value),
      pctFrac(o.p_net_positive.value),
      { node: el("span", { class: ciClass(o.mean_net.ci) }, pctFrac(o.mean_net.value, 2, true),
        el("span", { class: "small muted", text: " " + ciFrac(o.mean_net.ci, 2) })) },
      pctFrac(o.median_gross, 2, true),
      fan(o),
      `${o.samples.toLocaleString("fr-FR")} · ${o.p_up.blocks} blocs · ${o.regime_conditioned ? "même régime" : "tous régimes"}`,
    ];
    if (o.horizon === r.horizon) cells.rowClass = "selected";
    return cells;
  });
  return card("Ce qui s'est passé ensuite, dans des conditions comparables",
    el("p", { class: "muted small", text: `Moments passés de ${pair(r.symbol)} jusqu'au ${r.history_end} dans le même régime 1 h qu'à présent (tendance ${regime(r.context.trend_1h)}, volatilité ${regime(r.context.volatility_1h)}), sinon tous régimes (colonne Exemples). Intervalles à ${fmt(r.ci_level * 100, 1)} %, corrigés pour les 6 horizons. Coûts aller-retour : ${fmt(r.costs_round_trip_pct, 2)} %.` }),
    table([{ label: "Horizon" }, { label: "Hausse (fréquence)" }, { label: "Hausse, tous moments", num: true }, { label: "Gain net > 0", num: true },
      { label: "Gain net moyen" }, { label: "Médiane", num: true }, { label: "8 cas sur 10 entre" }, { label: "Exemples" }], lines, "aucune donnée"));
}

function strategiesCard(r) {
  const rows = (r.strategies || []).map((s) => {
    const action = s.action === "BUY" ? el("span", { class: "pill ok", text: "ACHAT (simulé)" })
      : el("span", { class: s.action === "ERREUR" ? "pill bad" : "pill muted", text: s.action === "NO_TRADE" ? "pas de trade" : s.action });
    const levels = s.levels ? `entrée ${price(s.levels.entry)} · stop ${price(s.levels.stop)} · objectif ${price(s.levels.targets[0])} · RR net ${fmt(s.levels.net_rr_tp1_central, 2)}` : "–";
    const verdict = s.walk_forward_verdict
      ? `${s.walk_forward_verdict}${isNum(s.walk_forward_expectancy_r) ? ` (${fmt(s.walk_forward_expectancy_r, 2, true)} R)` : ""}` : "jamais évaluée";
    return [{ node: el("strong", { text: s.strategy }) }, { node: action },
      [s.reason, ...(s.details || [])].filter(Boolean).join(" — ") || "–", levels,
      { node: el("span", { class: s.walk_forward_verdict === "VALIDATED_OOS" ? "ok" : "bad", text: verdict }) }];
  });
  return card("Avis des stratégies de CSI sur la dernière bougie",
    el("p", { class: "muted small", text: r.strategies_note || "" }),
    table(["Stratégie", "Décision", "Motif", "Niveaux", "Verdict du protocole"], rows, "aucune stratégie"));
}

function definitionsCard(r) {
  const defs = r.definitions || {};
  const labels = { historique: "Historique", comparable: "Moments comparables", p_up: "Hausse (fréquence)", p_net_positive: "Gain net > 0",
    mean_net: "Gain net moyen", fourchette: "Médiane et fourchette", plan: "Plan indicatif", issues: "Issues du plan", esperance: "Espérance",
    ecart: "Écart dû aux conditions actuelles", intervalle: "Intervalle", exemples: "Exemples et blocs", couts: "Coûts", etat: "État" };
  return el("section", { class: "card" }, el("details", {},
    el("summary", { text: "Définition de chaque chiffre" }),
    kv(Object.entries(defs).map(([k, v]) => [labels[k] || k, v])),
    el("p", { class: "muted small", text: r.cached ? "Résultat mis en cache jusqu'à la prochaine bougie." : "" })));
}

// --- onglet Marché ------------------------------------------------------------------------------------
function setHorizon(key) {
  state.horizon = key;
  for (const container of [document.getElementById("horizons"), document.getElementById("market-horizons")]) {
    for (const b of container.children) b.classList.toggle("on", b.dataset.key === key);
  }
}

function renderHorizonButtons(target) {
  target.replaceChildren(...state.horizons.map((h) => el("button", {
    type: "button", "data-key": h.key, class: h.key === state.horizon ? "on" : "", text: h.label,
    onclick: () => setHorizon(h.key),
  })));
}

async function runMarket() {
  const target = document.getElementById("market-result");
  const run = ++state.marketRun;
  const horizon = state.horizon;
  const body = el("tbody");
  const rows = {};
  for (const p of state.pairs) {
    const tr = el("tr", {}, el("td", {}, el("strong", { text: pair(p.symbol) })), el("td", { colspan: "7", class: "muted", text: "en attente…" }));
    rows[p.symbol] = tr;
    body.append(tr);
  }
  const label = (state.horizons.find((h) => h.key === horizon) || {}).label || horizon;
  target.replaceChildren(card(`Toutes les paires — horizon ${label}`, el("table", {},
    el("thead", {}, el("tr", {}, ["Paire", "Prix", "24 h", "Tendance · volatilité 1 h", "Hausse (fréquence)", "État du plan", "Espérance du plan", ""]
      .map((h) => el("th", { text: h })))), body)));
  for (const p of state.pairs) {
    if (run !== state.marketRun) return;                       // relancé entre-temps
    const tr = rows[p.symbol];
    try {
      // Mêmes nouveaux essais que l'onglet Opportunités : une API en redémarrage répond tout de suite par une erreur.
      let r = null;
      for (const wait of [0, 5000, 15000]) {
        if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
        if (run !== state.marketRun) return;
        try { r = await api("/analyze-pair", { symbol: p.symbol, horizon }); break; } catch (error) { if (wait === 15000) throw error; }
      }
      if (run !== state.marketRun) return;
      const plan = r.plan || {};
      const [kind, title] = PLAN_STATES[plan.state] || ["neutral", plan.state || "?"];
      const row = (r.overview || []).find((o) => o.horizon === horizon) || {};
      const detail = el("button", { type: "button", class: "ghost", text: "Détail", onclick: () => {
        document.getElementById("pair").value = p.symbol;
        openTab("pair");
        analyzePair();
      } });
      tr.replaceChildren(el("td", {}, el("strong", { text: pair(r.symbol) })), el("td", { class: "num", text: price(r.context.close) }),
        el("td", { class: "num", text: pctUnits(r.context.ret_24h_pct) }),
        el("td", { text: `${regime(r.context.trend_1h)} · ${regime(r.context.volatility_1h)}` }),
        el("td", {}, pctFrac(row.p_up && row.p_up.value), el("span", { class: "small muted", text: " " + ciFrac(row.p_up && row.p_up.ci) })),
        el("td", {}, el("span", { class: `pill ${kind === "neutral" ? "muted" : kind}`, text: title })),
        el("td", { class: ciClass(plan.expectancy_r_ci), text: `${fmt(plan.expectancy_r, 2, true)} R` }),
        el("td", {}, detail));
    } catch (error) {
      tr.replaceChildren(el("td", {}, el("strong", { text: pair(p.symbol) })), el("td", { colspan: "7", class: "error", text: error.message }));
    }
  }
}

// --- onglet Opportunités ------------------------------------------------------------------------------
const TP_OFTEN = 0.5;   // « objectif souvent atteint » : au moins la moitié des cas comparables (filtre d'affichage)

function opportunityRow(o) {
  const detail = el("button", { type: "button", class: "ghost", text: "Détail", onclick: () => {
    document.getElementById("pair").value = o.symbol;
    setHorizon(o.horizon);
    openTab("pair");
    analyzePair();
  } });
  const copyButton = el("button", { type: "button", class: "ghost", text: "Copier (pas un signal)" });
  copyButton.addEventListener("click", () => copy(o.copy_text || "", copyButton));
  return [{ node: el("strong", { text: pair(o.symbol) }) }, o.label || o.horizon,
    `${pctFrac(o.tp_first)} / ${pctFrac(o.sl_first)}`,
    { node: el("span", { class: ciClass(o.expectancy_r_ci), text: `${fmt(o.expectancy_r, 2, true)} R ${ciR(o.expectancy_r_ci)}` }) },
    o.entry_reference ? `${price(Number(o.entry_reference))} · ${price(Number(o.stop))} · ${price(Number(o.target))}` : (o.unavailable || "–"),
    `${(o.samples || 0).toLocaleString("fr-FR")} · ${o.blocks || 0} blocs`,
    { node: el("div", { class: "row" }, detail, o.copy_text ? copyButton : null) }];
}

function renderOpportunities(found, done, total) {
  const target = document.getElementById("opportunities-result");
  const headers = ["Paire", "Horizon", "Objectif / stop atteint d'abord", "Espérance par trade", "Entrée · stop · objectif", "Cas comparables", ""];
  const positive = found.plans.filter((o) => o.state === "HISTORIQUE_POSITIF_NON_VALIDE")
    .sort((a, b) => (b.expectancy_r_ci ? b.expectancy_r_ci[0] : -9) - (a.expectancy_r_ci ? a.expectancy_r_ci[0] : -9));
  const often = found.plans.filter((o) => isNum(o.tp_first) && o.tp_first >= TP_OFTEN
      && !["DONNEES_ANCIENNES", "INSUFFISANT"].includes(o.state))
    .sort((a, b) => b.tp_first - a.tp_first);
  const buys = found.buys.map((b) => [{ node: el("strong", { text: pair(b.symbol) }) }, b.strategy,
    b.levels ? `${price(b.levels.entry)} · ${price(b.levels.stop)} · ${price(b.levels.targets[0])}` : "–",
    { node: el("span", { class: "bad", text: b.walk_forward_verdict || "jamais évaluée" }) }]);
  target.replaceChildren(
    banner("warn", done < total ? `Analyse en cours : ${done} / ${total} paires` : `Analyse terminée : ${total} paires, ${found.plans.length} plans`,
      "Fréquences passées dans des conditions comparables, en échantillon, non validées : ce ne sont ni des signaux ni des propositions d'entrer.",
      el("div", { class: "small muted", text: found.errors.length ? `Paires illisibles : ${found.errors.join(", ")}` : "" })),
    card(`Historique positif, non validé (${positive.length})`,
      el("p", { class: "muted small", text: "Dans des conditions comparables, ce plan a gagné en moyenne, au-delà de la simple dérive passée (intervalle corrigé au-dessus de 0). Statistique en échantillon, jamais validée par le protocole." }),
      table(headers, positive.map(opportunityRow), "aucun plan à historique positif pour l'instant")),
    card(`Objectif atteint avant le stop dans au moins ${pctFrac(TP_OFTEN, 0)} des cas comparables (${often.length})`,
      el("p", { class: "muted small", text: "Fréquence passée, pas une probabilité : un objectif souvent atteint peut aller avec des pertes plus grosses que les gains ; regarder l'espérance et son intervalle." }),
      table(headers, often.map(opportunityRow), "aucun plan à ce niveau pour l'instant")),
    card(`Achats simulés des stratégies de CSI (${buys.length})`,
      el("p", { class: "muted small", text: "Simulation sur la dernière bougie close ; stratégies rejetées par le protocole (aucun avantage démontré)." }),
      table(["Paire", "Stratégie", "Entrée · stop · objectif", "Verdict du protocole"], buys, "aucun achat simulé")));
}

async function runOpportunities() {
  const run = ++state.opportunitiesRun;
  const button = document.getElementById("opportunities-run");
  button.disabled = true;
  const found = { plans: [], buys: [], errors: [] };
  const total = state.pairs.length;
  renderOpportunities(found, 0, total);
  try {
    for (const [index, p] of state.pairs.entries()) {
      if (run !== state.opportunitiesRun) return;
      try {
        // Une API momentanément absente (redémarrage du conteneur) répond tout de suite par une erreur : sans nouvel
        // essai, toutes les paires suivantes passaient « illisibles » en quelques secondes. Deux nouveaux essais,
        // 5 puis 15 s plus tard, avant de compter la paire comme illisible.
        let r = null;
        for (const wait of [0, 5000, 15000]) {
          if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
          if (run !== state.opportunitiesRun) return;
          try { r = await api("/opportunities/pair", { symbol: p.symbol }); break; } catch (error) { if (wait === 15000) throw error; }
        }
        if (run !== state.opportunitiesRun) return;                // relancé entre-temps : on s'efface
        for (const h of r.horizons || []) {
          if (!h.error) found.plans.push({ ...h, symbol: r.symbol });
        }
        for (const s of r.strategies || []) {
          if (s.action === "BUY") found.buys.push({ ...s, symbol: r.symbol });
        }
      } catch (_error) {
        found.errors.push(pair(p.symbol));
      }
      if (run !== state.opportunitiesRun) return;
      renderOpportunities(found, index + 1, total);
    }
  } finally {
    if (run === state.opportunitiesRun) button.disabled = false;
  }
}

// --- onglet Signal -----------------------------------------------------------------------------------
async function evaluateSignal() {
  const button = document.getElementById("evaluate");
  const target = document.getElementById("signal-result");
  const text = document.getElementById("signal-text").value;
  if (!text.trim()) return;
  button.disabled = true;
  busy(target, "Évaluation en cours…");
  try {
    renderSignal(await api("/evaluate", {
      text, source: document.getElementById("signal-source").value.trim() || "Manuel (tableau de bord)",
      record: document.getElementById("signal-record").checked,
      user_validated: document.getElementById("signal-validated").checked,
    }));
    state.followLoaded = false;
  } catch (error) {
    showError(target, error);
  } finally {
    button.disabled = false;
  }
}

function renderSignal(e) {
  const target = document.getElementById("signal-result");
  const [kind, title] = SIGNAL_VERDICTS[e.verdict] || ["neutral", e.verdict];
  const s = e.signal || {};
  const checks = el("ul", { class: "list checks" }, (e.checks || []).map((c) => el("li", {},
    el("span", { class: c.ok ? "ok" : "bad", text: c.ok ? "✔ " : "✘ " }), el("strong", { text: c.label }), " — " + c.detail)));
  const warnings = (e.warnings || []).length ? el("ul", { class: "list warn" }, e.warnings.map((w) => el("li", { text: w }))) : null;
  const g = e.geometry || {};
  const geometry = g.entry !== undefined ? kv([
    ["Entrée annoncée", price(g.entry)], ["Entrée obtenue (au plus le prix actuel)", price(g.entry_effective)],
    ["Écart au dernier prix", pctUnits(g.deviation_pct)], ["Stop", `${price(g.stop)} (${pctUnits(-g.stop_pct)}, ${fmt(g.stop_atr, 2)} ATR)`],
    ["Objectifs", (g.targets || []).map(price).join(" · ")], ["Objectif 1", pctUnits(g.tp1_pct)],
    ["Gain/risque brut", (g.rr_gross || []).map((x) => fmt(x, 2)).join(" · ")], ["Gain/risque net TP1 (coûts centraux)", fmt(g.rr_net_tp1_central, 2)],
    ...Object.entries(e.volatility || {}).map(([days, m]) => [`Ampleur prévue sur ${days} j : ±${fmt(m.move_pct, 2)} %`,
      `TP1 à ${fmt(m.tp1_moves, 2)} fois ce mouvement · stop à ${fmt(m.stop_moves, 2)} fois`]),
  ]) : el("p", { class: "muted", text: "géométrie non calculée (voir les contrôles)" });
  const b = e.base_rate;
  const base = b && b.samples ? kv([
    ["Ordres comparables", `${b.samples.toLocaleString("fr-FR")} remplis sur ${b.emitted.toLocaleString("fr-FR")} (${b.regime})`],
    ["Objectif 1 avant le stop", `${pctFrac(b.tp_first)} ${ciFrac(b.tp_first_ci95)}`],
    ["Stop avant l'objectif", pctFrac(b.sl_first)], ["Sortie au temps", pctFrac(b.timeout)],
    ["Espérance par ordre rempli", `${fmt(b.expectancy_r, 2, true)} R ${ciR(b.expectancy_r_ci95)}`, ciClass(b.expectancy_r_ci95)],
    ["Taux de remplissage de l'ordre limite", pctFrac(b.fill_rate)],
  ]) : el("p", { class: "muted", text: "aucun taux de base (signal refusé ou historique insuffisant)" });
  const c = e.context || {};
  const context = c.close !== undefined ? kv([
    ["Dernier prix", price(c.close)], ["Tendance 1 h", regime(c.trend_1h)], ["Volatilité 1 h", regime(c.volatility_1h)],
    ["RSI 14", fmt(c.rsi14, 1)], ["ATR 14", pctUnits(c.atr_pct, 3, false)], ["BTC sur 24 h", pctUnits(c.btc_ret_24h_pct)],
  ]) : el("p", { class: "muted", text: "contexte non calculé" });
  const stats = e.source_stats;
  const managements = (e.managements || []).length ? card("Autres gestions de ce signal (ordres pris au hasard, même géométrie)",
    table(["Gestion", { label: "R moyen", num: true }, { label: "Trades gagnants", num: true }],
      e.managements.map((m) => [{ node: el("span", { class: m.owner ? "ok" : "", text: m.label + (m.owner ? " (ta gestion)" : "") }) },
        `${fmt(m.r_mean, 2, true)} R`, pctFrac(m.win_share)]), ""),
    el("p", { class: "muted small", text: "Sur des entrées prises au hasard, aucune gestion ne crée d'avantage : ce tableau montre seulement comment la sortie change le résultat. Pour choisir une gestion, importe l'historique du groupe (carte ci-dessous) : CSI choisit sur le passé et vérifie sur les signaux récents." })) : null;
  target.replaceChildren(
    banner(kind, `${pair(s.symbol) || "Signal"} · ${title}`, e.summary_fr || ""),
    el("div", { class: "grid" },
      card("Contrôles", checks, warnings),
      card("Géométrie du signal", geometry),
      card("Taux de base de cette géométrie (sans sélection)", base,
        el("p", { class: "muted small", text: "Fréquence historique d'ordres identiques pris à l'aveugle sur cette paire : une référence, pas la probabilité que ce signal réussisse." })),
      card("Contexte de marché", context)),
    managements,
    el("p", { class: "muted small", text: (e.record_id ? `Enregistré (${e.record_id}) : son issue sera suivie automatiquement. ` : "Non enregistré. ")
      + (stats ? `Source « ${e.source} » : ${stats.evaluated || 0} signal(s) évalué(s), ${stats.resolved || 0} résolu(s).` : "") }));
}

// --- bilan d'un groupe sur son historique (export Telegram) ---------------------------------------------
function flattenTelegramText(text) {
  if (typeof text === "string") return text;
  if (Array.isArray(text)) return text.map((part) => (typeof part === "string" ? part : (part && part.text) || "")).join("");
  return "";
}

function slimExport(data) {
  // Seul le nécessaire part vers CSI : numéros (pour repérer les messages supprimés), heure, texte, modification.
  const chats = data && data.chats && Array.isArray(data.chats.list) ? data.chats.list : [data];
  return { chats: { list: chats.map((chat) => ({ name: chat && chat.name, id: chat && chat.id,
    messages: ((chat && chat.messages) || []).map((m) => (m && m.type === "message" ? {
      id: m.id, type: m.type, date_unixtime: m.date_unixtime, text: flattenTelegramText(m.text),
      ...(m.edited_unixtime ? { edited_unixtime: m.edited_unixtime } : {}),
      ...(m.forwarded_from ? { forwarded_from: m.forwarded_from } : {}),
    } : { id: m && m.id, type: m && m.type })) })) } };
}

async function runHistory() {
  const button = document.getElementById("history-run");
  const target = document.getElementById("history-result");
  const file = document.getElementById("history-file").files[0];
  if (!file) { target.replaceChildren(el("p", { class: "error", text: "Choisir d'abord le fichier result.json de l'export." })); return; }
  button.disabled = true;
  busy(target, "Lecture de l'export, puis rejeu de chaque signal sur les bougies de Binance (une à quelques minutes)…");
  try {
    let data;
    try { data = JSON.parse(await file.text()); } catch (_err) { throw new Error("ce fichier n'est pas un JSON (choisir le format JSON à l'export)"); }
    renderHistory(await api("/sources/history", { export: slimExport(data), weights: document.getElementById("history-weights").value }));
  } catch (error) {
    showError(target, error);
  } finally {
    button.disabled = false;
  }
}

function managementsBlock(study) {
  if (!study) return null;
  const top = (study.top_on_choice || []).map((m) => [m.label, `${fmt(m.r_mean_choice, 2, true)} R`, `${fmt(m.r_mean_confirm, 2, true)} R`]);
  const best = study.best, cur = study.current_confirm, diff = study.difference_confirm;
  return el("div", {},
    el("h3", { text: `Quelle gestion pour ce groupe ? (${study.variants} gestions comparées sur ${study.signals} signaux)` }),
    el("p", { class: "small", text: study.conclusion }),
    best ? el("p", { class: "muted small", text: `Choix sur ${study.choice_period.join(" → ")}, vérification sur ${study.confirm_period.join(" → ")} (jamais vue pendant le choix). `
      + `Sur la vérification : meilleure gestion ${fmt(best.confirm.r_mean, 2, true)} R ${ciR(best.confirm.ic95)}, ta gestion ${fmt(cur.r_mean, 2, true)} R ${ciR(cur.ic95)}, écart ${fmt(diff.r_mean, 2, true)} R ${ciR(diff.ic95)}.` }) : null,
    top.length ? table(["Gestion (5 meilleures sur les deux premiers tiers)", { label: "R moyen au choix", num: true }, { label: "R moyen à la vérification", num: true }], top, "") : null);
}

// Signaux lus sur IMAGE (audit avec les photos) : bilan à part, jamais dans le bilan du groupe ni dans sa preuve.
function imagesBlock(images, labels) {
  if (!images) return null;
  const rows = Object.entries(images.conventions || {}).filter(([, c]) => c.resolus).map(([key, c]) => [labels[key] || key,
    c.resolus, pctFrac(c.part_gagnants), `${fmt(c.r_moyen, 2, true)} R`, c.ic95 ? ciR(c.ic95) : "–", c.conclusion]);
  return el("div", {},
    el("h3", { text: `Signaux lus sur image : ${images.lues} lu(s), ${images.ignorees} ignoré(s) (lecture douteuse), ${images.mesurees} mesuré(s)` }),
    el("p", { class: "muted small", text: "Bilan à part : ces signaux ne comptent ni dans le tableau ci-dessus ni dans la preuve du groupe (taux d'erreur de lecture hors échantillon inconnu). À comparer au bilan des signaux texte ; contrôler à la main quelques lectures avant d'y croire." }),
    table(["Façon de jouer le signal", { label: "Résolus", num: true }, { label: "Gagnants", num: true }, { label: "R moyen", num: true }, "IC95", "Conclusion"],
      rows, "aucun signal lu sur image résolu"));
}

function renderHistory(result, intro) {
  const target = document.getElementById("history-result");
  const labels = result.conventions || {};
  const cards = Object.entries(result.summary || {}).map(([name, b]) => {
    const proof = b.preuve || {};
    const rows = Object.entries(b.conventions || {}).map(([key, c]) => [labels[key] || key, c.resolus, c.en_cours,
      c.resolus ? pctFrac(c.part_gagnants) : "–", c.resolus ? `${fmt(c.r_moyen, 2, true)} R` : "–",
      c.r_moyen_avec_ouvertes !== undefined ? `${fmt(c.r_moyen_avec_ouvertes, 2, true)} R` : "–",
      c.ic95 ? ciR(c.ic95) : "–", c.conclusion]);
    const deleted = b.messages_supprimes_part;
    return card(name === result.all ? "Tous les groupes" : name,
      el("p", { class: proof.proven ? "ok" : "warn", text: (proof.proven ? "✔ " : "• ") + (proof.text || "") }),
      el("p", { class: "muted small", text: `${b.messages} message(s) : ${Object.entries(b.statuts || {}).map(([k, v]) => `${k.toLowerCase().replace("_", " ")} ${v}`).join(", ")}`
        + (b.tp1_pct_moyen !== undefined ? ` · TP1 moyen +${fmt(b.tp1_pct_moyen, 2)} %, stop moyen −${fmt(b.stop_pct_moyen, 2)} % : il faut ${pctFrac(b.part_tp1_pour_etre_a_zero)} de TP1 atteints pour être à zéro, avant frais` : "")
        + (deleted !== null && deleted !== undefined ? ` · messages supprimés dans la numérotation : ${pctFrac(deleted)}` : "") }),
      table(["Façon de jouer le signal", { label: "Résolus", num: true }, { label: "En cours", num: true }, { label: "Gagnants", num: true },
        { label: "R moyen", num: true }, { label: "Avec positions ouvertes", num: true }, "IC95", "Conclusion"], rows, "aucun signal mesuré"),
      imagesBlock(b.images, labels),
      managementsBlock(b.gestions));
  });
  const detail = (result.rows || []).slice(-60).reverse().map((r) => [when(r.received_at), r.group + (r.from_image ? " (image)" : ""), pair(r.symbol),
    `−${fmt(r.stop_pct, 2)} % / +${fmt(r.tp1_pct, 2)} %`, ...["tp1_contact", "tp1_regle_du_signal", "echelle_bsm"].map((k) => {
      const o = (r.outcomes || {})[k] || {};
      return o.r === null || o.r === undefined ? (o.issue || "–") : `${o.issue} ${fmt(o.r, 2, true)} R`;
    })]);
  target.replaceChildren(intro || null, ...cards,
    card("Derniers signaux rejoués", table(["Publié", "Groupe", "Paire", "Stop / TP1", "TP1 au contact", "Stop à la clôture", "Comme le bot"], detail, "aucun signal lisible"),
      el("ul", { class: "list muted small" }, (result.notes || []).map((n) => el("li", { text: n }))),
      el("p", { class: "muted small", text: `Rapport complet : reports/${result.report}/ (${result.messages} messages lus).` })));
  state.followLoaded = false;
}

// --- audit d'un export copié dans exports/ (images lues sur la machine de CSI, en arrière-plan) ---------------
const EXPORT_POLL_MS = 5000;

function exportLabel(x) {
  const images = x.images_named ? `images : ${x.images_present} présente(s) sur ${x.images_named}` : "aucune image";
  return `${x.folder} — ${x.messages} message(s), ${images}` + (x.error ? " — illisible" : "");
}

function exportProgress(a) {
  if (!a) return "";
  const images = a.ocr
    ? ` · images lues : ${a.images_read} (${a.images_as_signals} signal/signaux)${a.images_present ? ` sur ${a.images_present} présente(s)` : ""}`
    : " · sans lecture des images";
  if (a.state === "EN_COURS") return `Audit en cours depuis ${when(a.started_at)} : ${a.step}${images}`;
  if (a.state === "ECHEC") return `Audit en échec (${when(a.finished_at)}) : ${a.error}`;
  return `Dernier audit terminé ${when(a.finished_at)} (lancé ${when(a.started_at)})${images}.`;
}

function selectedExport() {
  const folder = document.getElementById("export-folder").value;
  return ((state.exports || {}).exports || []).find((x) => x.folder === folder) || null;
}

function showExportStatus() {
  const status = document.getElementById("export-status");
  const button = document.getElementById("export-run");
  const d = state.exports || {};
  const x = selectedExport();
  status.className = "muted small";
  status.replaceChildren();
  if (!x) {
    status.textContent = d.directory ? `Aucun export dans ${d.directory} : y copier le dossier d'un export (result.json et photos).` : "";
    button.disabled = true;
    return;
  }
  const parts = [];
  if (x.error) parts.push(x.error);
  if (x.without_photos) parts.push(`${x.images_named} image(s) nommée(s), aucune présente : export fait sans les photos, le refaire en cochant « Photos » (les signaux texte seront quand même mesurés).`);
  if (d.ocr_available === false) parts.push("Lecture des images indisponible sur cette installation (extra « ocr ») : l'audit lirait le texte seulement.");
  if (x.audit) parts.push(exportProgress(x.audit));
  if (d.running && d.running !== x.folder) parts.push(`Un audit de « ${d.running} » est en cours : un seul à la fois.`);
  status.append(parts.join(" "));
  if (x.audit && x.audit.state === "TERMINE" && x.audit.has_result) {
    status.append(" ", el("button", { class: "pill", text: "Voir le résultat", onclick: () => showExportResult(x.folder) }));
  }
  button.disabled = Boolean(x.error || d.running);
}

async function loadExports() {
  const select = document.getElementById("export-folder");
  const status = document.getElementById("export-status");
  try {
    const d = await api("/sources/exports");
    state.exports = d;
    const current = select.value;
    select.replaceChildren(...(d.exports.length ? d.exports.map((x) => el("option", { value: x.folder, text: exportLabel(x) }))
      : [el("option", { value: "", text: "aucun export copié" })]));
    if (current && d.exports.some((x) => x.folder === current)) select.value = current;
    showExportStatus();
    if (d.running) watchExport(d.running);
  } catch (error) {
    status.className = "error small";
    status.textContent = "Erreur : " + error.message;
  }
}

function watchExport(folder) {
  state.exportWatch = folder;
  if (state.exportTimer) return;
  state.exportTimer = setInterval(pollExports, EXPORT_POLL_MS);
}

async function pollExports() {
  let d;
  try { d = await api("/sources/exports"); } catch (_err) { return; }
  state.exports = d;
  const x = (d.exports || []).find((e) => e.folder === state.exportWatch);
  const select = document.getElementById("export-folder");
  for (const option of select.options) {
    const entry = (d.exports || []).find((e) => e.folder === option.value);
    if (entry) option.textContent = exportLabel(entry);
  }
  showExportStatus();
  if (!x || !x.audit || x.audit.state !== "EN_COURS") {
    clearInterval(state.exportTimer);
    state.exportTimer = null;
    if (x && x.audit && x.audit.state === "TERMINE") showExportResult(x.folder);
    else if (x && x.audit && x.audit.state === "ECHEC") showError(document.getElementById("history-result"), new Error(x.audit.error || "audit en échec"));
  }
}

async function showExportResult(folder) {
  const target = document.getElementById("history-result");
  busy(target, "Chargement du résultat…");
  try {
    const r = await api("/sources/exports/audit?folder=" + encodeURIComponent(folder));
    const a = r.audit || {};
    if (!a.result) throw new Error(a.error || "résultat indisponible");
    // Le résultat peut dater (relu au redémarrage) : la preuve qui compte est celle d'aujourd'hui.
    const proofs = Object.entries(r.proofs_now || {}).map(([name, p]) => el("li", { class: p.proven ? "ok" : "warn",
      text: `${name} : ${p.generated_at ? `preuve enregistrée le ${when(p.generated_at)}, valable jusqu'au ${when(p.expires_at)}${p.expired ? " (EXPIRÉE)" : ""} ; ` : ""}état actuel : ${p.text}` }));
    const intro = el("section", { class: "card" },
      el("h2", { text: `Audit du dossier « ${folder} », images comprises` }),
      el("p", { class: "muted small", text: `${exportProgress(a)} Images nommées dans l'export : ${a.images_named}, présentes : ${a.images_present}, lues : ${a.images_read}, lues comme signaux : ${a.images_as_signals}. Ventes aux objectifs : ${a.weights === "equal" ? "parts égales" : "davantage aux premiers objectifs"}.` }),
      proofs.length ? el("ul", { class: "list small" }, proofs) : null);
    renderHistory(a.result, intro);
  } catch (error) {
    showError(target, error);
  }
}

async function runExportAudit() {
  const button = document.getElementById("export-run");
  const target = document.getElementById("history-result");
  const x = selectedExport();
  if (!x) { target.replaceChildren(el("p", { class: "error", text: "Choisir d'abord un dossier d'export." })); return; }
  button.disabled = true;
  busy(target, "Audit lancé : lecture des images sur la machine de CSI, puis rejeu de chaque signal sur les bougies de Binance (plusieurs minutes pour des milliers de photos). L'avancement s'affiche au-dessus toutes les 5 secondes.");
  try {
    const ocr = (state.exports || {}).ocr_available !== false;
    await api("/sources/exports/audit", { folder: x.folder, weights: document.getElementById("history-weights").value, ocr });
    await loadExports();
    watchExport(x.folder);
  } catch (error) {
    showError(target, error);
    button.disabled = false;
  }
}

// --- onglet Suivi --------------------------------------------------------------------------------------
function verdictClass(verdict) {
  const v = String(verdict || "");
  if (/^(VALIDATED|USEFUL_OOS)/.test(v)) return "ok";
  if (/^SYSTEME_ADMISSIBLE|^PREVISION_UTILE|INTERESSANT_|^[1-9]\d* (CONDITION|PISTE)/.test(v)) return "warn";    // en échantillon : jamais vert
  if (/REJECTED|NOT_USEFUL|AUCUN|ÉCHEC|INCONCLUSIVE/.test(v)) return "bad";
  return "muted";
}

function forwardCard(report) {
  if (!report) return card("Tests en direct", el("p", { class: "muted small", text: "indisponible" }));
  const rows = [];
  for (const t of report.tests || []) {
    const h = ((t.stats || {}).horizons || {});
    const main = (h["24h"] || {}).observe || {};
    const sc = ((t.stats || {}).scenarios || {}).central;
    const st = t.stats || {};
    const onchain = st.mints !== undefined ? st : null;
    const providers = st.providers ? st.providers : null;
    const followA = st.light_share ? st : null;
    const followCentral = followA ? ((followA.scenarios || {}).central || {}) : {};
    const followText = (v) => { const s = followCentral[v] || {}; return `${v} ${isNum(s.return) ? fmt(s.return * 100, 2, true) + " %" : "–"}`; };
    const whole = providers ? (providers.ensemble || {}) : {};
    const wholeCentral = ((whole.scenarios || {}).central) || {};
    const excessText = (h) => { const s = (sc || {})[h] || {}; return `${h} : ${isNum(s.excess) ? fmt(s.excess * 100, 2, true) + " %" : "–"} (${s.n || 0})`; };
    // F5 et F8 rangent aussi des écarts scalaires (A_FEU_minus_A, A_NEWS_minus_A), parfois null : seules les variantes
    // d'échelle (objets avec « filled ») entrent dans ce résumé.
    const ladder = sc && !onchain ? Object.entries(sc).filter(([, s]) => s && typeof s === "object" && "filled" in s)
      .map(([v, s]) => `${v.replace("echelle_", "")} : ${isNum(s.r_mean) ? fmt(s.r_mean, 2, true) + " R" : "–"} (${s.filled || 0})`).join(" · ") : null;
    rows.push([t.test_id, t.state, t.started_at ? when(t.started_at) : "–", t.final_at ? when(t.final_at) : "–",
      t.journal && t.journal.ok ? `intègre (${t.journal.entries})` : { node: el("span", { class: "bad", text: "ROMPU" }) },
      st.news_items !== undefined ? `${st.news_items} news avec terme (${st.applied} appliquée(s), ${st.ambiguous} ambiguë(s)), ${st.days} jour(s) ; A + news − A ${isNum(((st.scenarios || {}).central || {}).A_NEWS_minus_A) ? fmt(st.scenarios.central.A_NEWS_minus_A * 100, 2, true) + " %" : "–"}`
        : st.listings !== undefined ? `${st.listings} listing(s), ${st.decisions} joué(s), ${st.pending} en attente ; rendement 24 h ${isNum((((st.scenarios || {}).central || {})["24h"] || {}).r_mean) ? fmt(st.scenarios.central["24h"].r_mean * 100, 2, true) + " %" : "–"} (${(((st.scenarios || {}).central || {})["24h"] || {}).n || 0})`
        : st.groups !== undefined && st.overall !== undefined ? `${st.figures} figure(s) (${st.bull} haussière(s), ${st.bear} baissière(s)), ${st.orders} ordre(s) : ${st.executed} exécuté(s), ${st.cancelled} annulé(s), ${st.pending} en cours ; ensemble : R moyen ${isNum((st.overall.scenarios.central || {}).r_mean) ? fmt(st.overall.scenarios.central.r_mean, 2, true) : "–"}, excès sur les placebos ${isNum((st.overall.scenarios.central || {}).placebo_excess) ? fmt(st.overall.scenarios.central.placebo_excess, 2, true) + " R" : "–"}`
        : st.comparisons !== undefined ? `${st.checks} prévision(s) journalisée(s), ${st.resolved} résolue(s), ${st.pending} en attente ; ${Object.values(st.comparisons).filter((c) => c.expected === "CONFIRME").map((c) => `${c.horizon} ${c.candidate.split("_")[0]} ${isNum(c.diff) ? fmt(c.diff, 3, true) : "–"} (${c.days} j)`).join(" · ")}`
        : st.checks !== undefined ? `${st.checks} contrôle(s), ${st.events} événement(s), ${st.decisions} joué(s), ${st.pending} en attente ; excès ${["3j", "72h", "168h"].map((h) => (((st.scenarios || {}).central || {})[h] || {})).map((s, i) => isNum(s.excess) ? `${["3 j", "72 h", "168 h"][i]} ${fmt(s.excess * 100, 2, true)} %` : null).filter(Boolean).join(", ") || "–"}`
        : followA ? `${followA.decisions} décision(s), ${followA.days} jour(s) ; feu : ${Object.entries(followA.light_days || {}).map(([k, v]) => `${k} ${v}`).join(", ")} ; ${["A", "A_FEU", "STATIQUE"].map(followText).join(" · ")} ; conformité ${(followA.conformity || {}).status || "–"}`
        : providers ? `${st.signals} message(s), ${st.decisions} joué(s), ${st.pending} en attente, ${Object.keys(providers).length - 1} fournisseur(s) ; ensemble : ${wholeCentral.n || 0} résolu(s), R moyen ${isNum(wholeCentral.r_mean) ? fmt(wholeCentral.r_mean, 2, true) : "–"} ${ciR(wholeCentral.r_ci95)}, excès sur les placebos ${isNum(wholeCentral.placebo_excess) ? fmt(wholeCentral.placebo_excess, 2, true) + " R" : "–"}`
        : onchain ? `${onchain.mints} création(s) lue(s), ${onchain.events} événement(s), ${onchain.decisions} décidé(s), ${onchain.pending} en attente ; excès net sur les placebos : ${["24h", "72h"].map(excessText).join(" · ")}`
        : ladder ? `R moyen par nombre d'objectifs (remplis) : ${ladder}` : main.n ? `${main.n} décisions, remplissage ${pctFrac(main.fill_rate)}, écart ${fmt(main.diff, 3, true)} R ${ciR(main.diff_ci)}, équilibre ${isNum(main.break_even_bps) ? fmt(main.break_even_bps, 1) + " pb" : "–"}` : "–",
      (t.stats || {}).verdict || "–"]);
  }
  const log = report.derivatives_log || {};
  return card("Tests en direct (pré-inscrits)",
    el("p", { class: "muted small", text: report.warning }),
    table(["Test", "État", "Démarré", "Évaluation", "Journal", "Mesure provisoire", "Verdict"], rows, "aucun test"),
    el("p", { class: "muted small", text: `Relevé quotidien du financement et de l'intérêt ouvert : ${log.days || 0} jour(s)`
      + (log.last ? `, dernier ${log.last.day} (${log.last.pairs} paires)` : "") + `. ${report.unlocks}` }));
}

const OUTCOME_LABELS = { TP1_FIRST: "objectif atteint", SL_FIRST: "stop touché", TIMEOUT: "sortie à 24 h",
  UNFILLED: "non rempli", TROU: "données manquantes" };

async function loadFollow(force = false) {
  const target = document.getElementById("follow-result");
  if (state.followLoaded && !force) return;
  busy(target, "Chargement…");
  try {
    const [health, models, recent, sources, generated, universe, admissions, history, plans, forward, relay] = await Promise.all([
      api("/health"), api("/models"), api("/signals/recent?limit=15"), api("/sources"), api(`/signals/generated?limit=${GENERATED_PAGE}`), api("/universe"),
      refreshAdmissions(), api("/sources/history"), api("/plans/live"), api("/forward").catch(() => null),
      api("/telegram/relay").catch(() => null),
    ]);
    state.followLoaded = true;
    state.models = models;
    const refresh = el("button", { type: "button", class: "ghost", text: "Actualiser", onclick: () => loadFollow(true) });
    target.replaceChildren(
      el("div", { class: "row" }, refresh),
      el("div", { class: "grid" },
        card("Surveillance", kv([
          ["État", health.ready ? (health.degraded ? "dégradée" : "à jour") : "arrêtée ou pas prête", health.ready && !health.degraded ? "ok" : "bad"],
          ["Détail", health.detail || "–"], ["Ordres passés par CSI", "jamais (analyse seulement)"],
        ])),
        card("Univers", kv([
          ["Paires configurées", (universe.configured || []).map(pair).join(", ")],
          ["Paires ajoutées", (universe.user_pairs || []).map((p) => `${pair(p.symbol)} (${p.status})`).join(", ") || "aucune"],
        ]))),
      card("Verdicts des modèles", el("p", { class: "muted small", text: `${models.note} Essais comptés sur DEVELOPMENT : `
          + (isNum(models.research_program_trials) ? `registre de recherche ${models.research_program_trials}, ` : "")
          + `registre de la surveillance ${models.program_trials}.` }),
        table(["Modèle", "Stratégie", "Verdict", "Date", "Registre"], (models.models || []).map((m) => [m.label, m.strategy,
          { node: el("span", {}, el("strong", { class: verdictClass(m.verdict), text: m.verdict || m.status }),
            m.detail ? el("div", { class: "small muted", text: m.detail }) : null) }, when(m.created_at), m.source]),
        "aucun modèle évalué")),
      card("Suivi en direct des plans indicatifs",
        el("p", { class: "muted small", text: `Chaque jour, le plan de chaque paire (1, 3 et 7 jours) est enregistré puis suivi sur les bougies qui arrivent ensuite : des données que personne n'avait vues. ${plans.rule || ""}.` }),
        table(["Horizon", "État au moment du plan", { label: "Enregistrés", num: true }, { label: "Terminés", num: true }, { label: "R moyen", num: true }, "IC95", { label: "Gagnants", num: true }, "Preuve"],
          (plans.groups || []).map((g) => [g.label, (PLAN_STATES[g.state] || [, g.state])[1], g.recorded, g.resolved,
            g.resolved ? `${fmt(g.r_mean, 2, true)} R` : "–", ciR(g.ic95), g.resolved ? pctFrac(g.win_share) : "–",
            { node: el("span", { class: g.proven ? "ok" : "muted", text: g.proven ? "prouvé en direct" : g.progress }) }]),
          "aucun plan encore enregistré : le premier passage a lieu chaque jour après 00:10 UTC")),
      relayCard(relay),
      forwardCard(forward),
      card("Signaux évalués récemment", table(["Reçu", "Source", "Paire", "Entrée · stop · TP1", "Avis", "Issue", "R"],
        (recent.signals || []).map((x) => [when(x.received_at), x.source, pair(x.symbol), `${price(x.entry)} · ${price(x.stop)} · ${price(x.tp1)}`,
          x.verdict, x.outcome || "en cours", fmt(x.outcome_r, 2, true)]), "aucun signal évalué")),
      card("Bilan des groupes (contre le taux de base)", el("p", { class: "muted small", text: sources.rule }),
        table(["Source", "Évalués", "Résolus", "TP1 réel / base", "R réel / base", "Écart [IC95]", "Conclusion"],
          (sources.sources || []).map((x) => [x.source, x.evaluated, x.resolved, `${pctFrac(x.tp1_real)} / ${pctFrac(x.tp1_base)}`,
            `${fmt(x.r_real, 2, true)} / ${fmt(x.r_base, 2, true)}`, `${fmt(x.edge_r, 2, true)} ${ciR(x.edge_ci95)}`, x.conclusion]), "aucune source")),
      card("Signaux trouvés par les stratégies de CSI (shadow)", el("p", { class: "muted small", text: generated.note || "" }),
        table(["Stratégie", { label: "Résolus", num: true }, { label: "Remplis", num: true }, { label: "R moyen des remplis", num: true }, { label: "Gagnants", num: true }],
          (generated.summary || []).map((s) => [s.strategy, s.resolved, s.filled, isNum(s.r_mean) ? `${fmt(s.r_mean, 2, true)} R` : "–", pctFrac(s.win_share)]),
          "aucun signal encore résolu (24 h après la fin de validité de l'entrée)"),
        generatedBrowser(generated)),
      groupsCard(history),
      admissionsCard(admissions));
    if (state.scrollToAdmissions) {
      state.scrollToAdmissions = false;
      document.getElementById("admissions-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    showError(target, error);
  }
}

// Repli : une ligne de résumé, le détail au clic (cartes longues en fin de page).
function folded(summary, ...children) {
  return el("details", {}, el("summary", { text: summary }), ...children);
}

// Relais Telegram : les messages arrivent-ils ? (dernier reçu, nombre par groupe sur 24 h et 7 jours)
function relayCard(relay) {
  if (!relay) return card("Relais Telegram", el("p", { class: "muted", text: "état du relais indisponible" }));
  const silent = relay.silent_hours;
  const status = relay.last_received_at === null
    ? el("p", { class: "warn", text: "aucun message reçu sur 7 jours : vérifier que le relais tourne sur le PC (systemctl --user status relais-telegram)" })
    : el("p", { class: silent > 12 ? "warn" : "ok", text: `Dernier message reçu ${when(relay.last_received_at)}`
        + (silent > 12 ? ` — aucun depuis ${fmt(silent, 1)} h : vérifier le relais (systemctl --user status relais-telegram)` : "")
        + ` · ${relay.day} sur 24 h · ${relay.week} sur 7 jours` });
  return card("Relais Telegram", el("p", { class: "muted small", text: relay.note }), status,
    table(["Groupe ou conversation", { label: "24 h", num: true }, { label: "7 jours", num: true }, "Dernier message"],
      (relay.groups || []).map((g) => [g.group, g.day, g.week, when(g.last)]), "aucun message"));
}

function groupsCard(history) {
  const groups = history.groups || [];
  const proven = groups.filter((g) => g.proven).length;
  return card("Groupes Telegram : avis lié au groupe",
    el("p", { class: "muted small", text: "Un groupe prouvé (en direct, ou sur son historique importé depuis l'onglet « Évaluer un signal ») rend ses signaux favorables malgré une géométrie défavorable. Preuve sur historique valable 30 jours." }),
    groups.length
      ? folded(`${groups.length} groupe${groups.length > 1 ? "s" : ""}, ${proven} prouvé${proven > 1 ? "s" : ""} — afficher le détail`,
        table(["Groupe", "Preuve sur historique", "Importé le"], groups.map((g) => [g.source,
          { node: el("span", { class: g.proven ? "ok" : "warn", text: g.text || "–" }) }, when(g.generated_at)]), ""))
      : el("p", { class: "muted", text: "aucun historique importé : exporte un groupe depuis Telegram Desktop (JSON) puis importe-le dans « Évaluer un signal »" }));
}

// --- signaux des stratégies de CSI : tous consultables, par pages de 20, filtrables par stratégie ----------
const GENERATED_PAGE = 20;

function generatedTable(data) {
  return table(["Créé", "Paire", "Stratégie", "Entrée · stop · TP1", "Verdict stratégie", "Issue", { label: "R", num: true }],
    (data.signals || []).map((x) => [when(x.created_at), pair(x.symbol), x.strategy, `${price(x.entry)} · ${price(x.stop_loss)} · ${price(x.targets[0])}`,
      x.strategy_verdict || "–", OUTCOME_LABELS[x.outcome] || (x.expired ? "en cours de suivi" : "actif"),
      isNum(x.outcome_r) ? `${fmt(x.outcome_r, 2, true)} R` : "–"]), "aucun signal trouvé");
}

function generatedBrowser(first) {
  const view = { offset: 0, strategy: "" };
  const holder = el("div", {});
  const status = el("span", { class: "muted small" });
  const newer = el("button", { type: "button", class: "ghost", text: "← plus récents" });
  const older = el("button", { type: "button", class: "ghost", text: "plus anciens →" });
  const select = el("select", { "aria-label": "Stratégie" }, el("option", { value: "", text: "toutes les stratégies" }),
    (first.strategies || []).map((name) => el("option", { value: name, text: name })));
  function render(data) {
    const total = data.total || 0;
    const shown = (data.signals || []).length;
    status.textContent = total ? `signaux ${view.offset + 1} à ${view.offset + shown} sur ${total}, du plus récent au plus ancien` : "";
    newer.disabled = view.offset === 0;
    older.disabled = view.offset + GENERATED_PAGE >= total;
    holder.replaceChildren(generatedTable(data));
  }
  async function load() {
    busy(holder, "Chargement…");
    try {
      const query = `limit=${GENERATED_PAGE}&offset=${view.offset}` + (view.strategy ? `&strategy=${encodeURIComponent(view.strategy)}` : "");
      render(await api(`/signals/generated?${query}`));
    } catch (error) {
      showError(holder, error);
    }
  }
  newer.addEventListener("click", () => { view.offset = Math.max(0, view.offset - GENERATED_PAGE); load(); });
  older.addEventListener("click", () => { view.offset += GENERATED_PAGE; load(); });
  select.addEventListener("change", () => { view.strategy = select.value; view.offset = 0; load(); });
  render(first);
  return el("div", {}, el("div", { class: "pager" }, select, newer, older, status), holder);
}

// --- univers : avis halal et décisions d'ajout ---------------------------------------------------------
async function refreshAdmissions() {
  const badge = document.getElementById("admissions-badge");
  try {
    const data = await api("/admissions");
    const count = (data.pending || []).length;
    badge.textContent = `${count} crypto${count > 1 ? "s" : ""} à décider`;
    badge.classList.toggle("hidden", count === 0);
    return data;
  } catch (_error) {
    badge.classList.add("hidden");
    return null;
  }
}

async function decideAdmission(symbol, decision, button) {
  button.disabled = true;
  try {
    await api("/admissions/decide", { symbol, decision });
    state.followLoaded = false;
    await refreshAdmissions();
    await loadFollow(true);
  } catch (error) {
    button.disabled = false;
    button.textContent = `Erreur : ${error.message}`;
  }
}

async function decideAllAdmissions(button, symbols) {
  button.disabled = true;
  button.textContent = "Ajout en cours…";
  try {
    const out = await api("/admissions/decide-all", symbols ? { symbols } : {});
    const c = out.counts || {};
    button.textContent = `Fait : ${c.AJOUTEE || 0} ajoutée(s), ${c.INDISPONIBLE || 0} indisponible(s)`;
    await refreshAdmissions();
    state.followLoaded = false;
    setTimeout(() => loadFollow(true), 1500);
  } catch (error) {
    button.disabled = false;
    button.textContent = `Erreur : ${error.message}`;
  }
}

async function runAdmissions(button) {
  button.disabled = true;
  button.textContent = "Vérification sur Binance et application du screening…";
  try {
    const out = await api("/admissions/run", {});
    const c = out.counts || {};
    button.textContent = `Fait : ${c.AJOUTEE || 0} ajoutée(s), ${c.REFUSEE || 0} refusée(s), ${c.A_DECIDER || 0} à décider, ${c.INDISPONIBLE || 0} indisponible(s)`;
    await refreshAdmissions();
    state.followLoaded = false;
    setTimeout(() => loadFollow(true), 1500);
  } catch (error) {
    button.disabled = false;
    button.textContent = `Erreur : ${error.message}`;
  }
}

const ADMISSION_GROUPS = [
  ["une_source", "Une source dit halal, aucune réserve", "Une seule des trois sources la classe halal ; les autres ne la listent pas. La règle en demande deux pour un ajout direct."],
  ["douteux", "Zone grise pour une source au moins", "Une source la place en zone grise (avis partagés ou activité en partie non conforme)."],
  ["aucune_source", "Aucune source ne la liste", "Aucune des trois sources ne donne d'avis : rien ne permet de conclure."],
];
const ADMISSION_LABELS = { AJOUTEE: ["ok", "ajoutée"], REFUSEE: ["bad", "refusée"], INDISPONIBLE: ["muted", "indisponible sur Binance"], A_DECIDER: ["warn", "à décider"] };

function admissionsCard(data) {
  if (!data) return card("Univers : avis halal", el("p", { class: "muted small", text: "indisponible" }));
  const run = el("button", { type: "button", class: "primary", text: "Appliquer le screening (ajouter les favorables)" });
  run.addEventListener("click", () => runAdmissions(run));
  const addAll = el("button", { type: "button", class: "ghost", text: "Tout ajouter (ma décision)" });
  addAll.addEventListener("click", () => decideAllAdmissions(addAll));
  const pendingRow = (p) => {
    const add = el("button", { type: "button", class: "primary", text: "Ajouter" });
    const refuse = el("button", { type: "button", class: "ghost", text: "Refuser" });
    add.addEventListener("click", () => decideAdmission(p.symbol, "add", add));
    refuse.addEventListener("click", () => decideAdmission(p.symbol, "refuse", refuse));
    const sources = Object.entries(p.sources || {}).map(([code, verdict]) => `${code} ${verdict}`).join(", ") || "aucune source";
    return [{ node: el("strong", { text: pair(p.symbol) }) }, sources, p.reason, { node: el("div", { class: "row" }, add, refuse) }];
  };
  const all = data.pending || [];
  // Du plus étayé au moins étayé ; chaque groupe a son bouton d'ajout (décision du propriétaire).
  const groups = ADMISSION_GROUPS.map(([key, title, hint]) => {
    const rows = all.filter((p) => (p.group || "aucune_source") === key);
    if (!rows.length) return null;
    const bulk = el("button", { type: "button", class: "ghost", text: `Ajouter ces ${rows.length} cryptos (ma décision)` });
    bulk.addEventListener("click", () => decideAllAdmissions(bulk, rows.map((p) => p.symbol)));
    return el("div", {}, el("h3", { text: `${title} (${rows.length})` }), el("p", { class: "muted small", text: hint }),
      el("div", { class: "row" }, bulk),
      table(["Paire", "Avis des sources", "Motif", "Ta décision"], rows.map(pendingRow), "rien à décider"));
  });
  const pending = all;
  const decisions = (data.decisions || []).map((d) => {
    const [kind, label] = ADMISSION_LABELS[d.decision] || ["muted", d.decision];
    return [pair(d.symbol), { node: el("span", { class: `pill ${kind}`, text: label }) }, d.decided_by, d.reason, when(d.decided_at)];
  });
  const section = card("Univers : avis halal et décisions d'ajout",
    folded("Règle du screening — afficher", el("p", { class: "muted small", text: data.rule })),
    el("div", { class: "row" }, run, (data.pending || []).length ? addAll : null),
    el("h3", { text: `Cryptos à décider (${pending.length})` }),
    pending.length ? null : el("p", { class: "muted", text: "rien à décider" }),
    ...groups,
    decisions.length
      ? folded(`Décisions déjà prises (${decisions.length}) — afficher`,
        table(["Paire", "Décision", "Par", "Motif", "Date"], decisions, ""))
      : el("p", { class: "muted", text: "aucune décision pour l'instant" }));
  section.id = "admissions-card";
  return section;
}

// --- navigation et démarrage ---------------------------------------------------------------------------
function openTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    const on = tab.dataset.tab === name;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  }
  for (const pane of document.querySelectorAll(".tabpane")) pane.classList.toggle("hidden", pane.id !== `tab-${name}`);
  if (name === "follow") loadFollow();
  if (name === "signal") { loadImages(); loadExports(); }
  if (name === "market") { loadTechnical(); loadVolatility(); loadRisk(); }
}

function start() {
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => openTab(tab.dataset.tab));
  document.getElementById("analyze").addEventListener("click", analyzePair);
  document.getElementById("market-run").addEventListener("click", runMarket);
  document.getElementById("opportunities-run").addEventListener("click", runOpportunities);
  document.getElementById("evaluate").addEventListener("click", evaluateSignal);
  document.getElementById("history-run").addEventListener("click", runHistory);
  document.getElementById("export-run").addEventListener("click", runExportAudit);
  document.getElementById("export-refresh").addEventListener("click", loadExports);
  document.getElementById("export-folder").addEventListener("change", showExportStatus);
  document.getElementById("token-save").addEventListener("click", () => {
    try { localStorage.setItem(TOKEN_KEY, document.getElementById("token").value.trim()); } catch (_err) { /* stockage bloqué */ }
    document.getElementById("token-box").classList.add("hidden");
    boot();
  });
  document.getElementById("admissions-badge").addEventListener("click", () => {
    state.scrollToAdmissions = true;
    if (state.followLoaded) {
      openTab("follow");
      state.scrollToAdmissions = false;
      document.getElementById("admissions-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      openTab("follow");
    }
  });
  boot();
  setInterval(refreshHealth, 60000);
  setInterval(refreshAdmissions, 300000);
}

async function boot() {
  refreshHealth();
  refreshAdmissions();
  const params = new URLSearchParams(window.location.search);
  const tab = { signal: "signal", suivi: "follow", marche: "market", opportunites: "opportunities" }[params.get("onglet")];
  if (tab) openTab(tab);
  try {
    await loadPairs();
  } catch (error) {
    showError(document.getElementById("pair-result"), error);
    return;
  }
  if (tab === "market" && params.get("lancer") === "1") runMarket();
  if (tab === "opportunities" && params.get("lancer") === "1") runOpportunities();
  const wanted = (params.get("paire") || "").toUpperCase().replace("/", "");
  if (wanted && state.pairs.some((p) => p.symbol === wanted)) {
    document.getElementById("pair").value = wanted;
    const horizon = params.get("horizon");
    if (horizon && state.horizons.some((h) => h.key === horizon)) setHorizon(horizon);
    analyzePair();
  }
}

document.addEventListener("DOMContentLoaded", start);
