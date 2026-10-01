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
  target.replaceChildren(head, el("div", { class: "grid" }, contextCard(r), planCard(r)), futures, overviewCard(r),
    strategiesCard(r), forecast, definitionsCard(r));
  futures.replaceChildren(card("Marché à terme — positionnement du moment", el("p", { class: "muted small", text: "Lecture des données publiques du marché à terme…" })));
  derivativesCard(r.symbol).then((c) => futures.replaceChildren(c), (error) => futures.replaceChildren(
    card("Marché à terme — positionnement du moment", el("p", { class: "muted small", text: `Indisponible : ${error.message}` }))));
  forecastCard().then((c) => forecast.replaceChildren(c), () => forecast.replaceChildren());
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
      const r = await api("/analyze-pair", { symbol: p.symbol, horizon });
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
  const often = found.plans.filter((o) => isNum(o.tp_first) && o.tp_first >= TP_OFTEN && o.state !== "DONNEES_ANCIENNES")
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
        const r = await api("/opportunities/pair", { symbol: p.symbol });
        for (const h of r.horizons || []) {
          if (!h.error) found.plans.push({ ...h, symbol: r.symbol });
        }
        for (const s of r.strategies || []) {
          if (s.action === "BUY") found.buys.push({ ...s, symbol: r.symbol });
        }
      } catch (_error) {
        found.errors.push(pair(p.symbol));
      }
      renderOpportunities(found, index + 1, total);
    }
  } finally {
    button.disabled = false;
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
  target.replaceChildren(
    banner(kind, `${pair(s.symbol) || "Signal"} · ${title}`, e.summary_fr || ""),
    el("div", { class: "grid" },
      card("Contrôles", checks, warnings),
      card("Géométrie du signal", geometry),
      card("Taux de base de cette géométrie (sans sélection)", base,
        el("p", { class: "muted small", text: "Fréquence historique d'ordres identiques pris à l'aveugle sur cette paire : une référence, pas la probabilité que ce signal réussisse." })),
      card("Contexte de marché", context)),
    el("p", { class: "muted small", text: (e.record_id ? `Enregistré (${e.record_id}) : son issue sera suivie automatiquement. ` : "Non enregistré. ")
      + (stats ? `Source « ${e.source} » : ${stats.evaluated || 0} signal(s) évalué(s), ${stats.resolved || 0} résolu(s).` : "") }));
}

// --- onglet Suivi --------------------------------------------------------------------------------------
function verdictClass(verdict) {
  const v = String(verdict || "");
  if (/^(VALIDATED|USEFUL_OOS)/.test(v)) return "ok";
  if (/^SYSTEME_ADMISSIBLE|^[1-9]\d* (CONDITION|PISTE)/.test(v)) return "warn";    // en échantillon : jamais vert
  if (/REJECTED|NOT_USEFUL|AUCUN|ÉCHEC|INCONCLUSIVE/.test(v)) return "bad";
  return "muted";
}

async function loadFollow(force = false) {
  const target = document.getElementById("follow-result");
  if (state.followLoaded && !force) return;
  busy(target, "Chargement…");
  try {
    const [health, models, recent, sources, generated, universe, admissions] = await Promise.all([
      api("/health"), api("/models"), api("/signals/recent?limit=15"), api("/sources"), api("/signals/generated?limit=10"), api("/universe"),
      refreshAdmissions(),
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
      admissionsCard(admissions),
      card("Signaux évalués récemment", table(["Reçu", "Source", "Paire", "Entrée · stop · TP1", "Avis", "Issue", "R"],
        (recent.signals || []).map((x) => [when(x.received_at), x.source, pair(x.symbol), `${price(x.entry)} · ${price(x.stop)} · ${price(x.tp1)}`,
          x.verdict, x.outcome || "en cours", fmt(x.outcome_r, 2, true)]), "aucun signal évalué")),
      card("Bilan des groupes (contre le taux de base)", el("p", { class: "muted small", text: sources.rule }),
        table(["Source", "Évalués", "Résolus", "TP1 réel / base", "R réel / base", "Écart [IC95]", "Conclusion"],
          (sources.sources || []).map((x) => [x.source, x.evaluated, x.resolved, `${pctFrac(x.tp1_real)} / ${pctFrac(x.tp1_base)}`,
            `${fmt(x.r_real, 2, true)} / ${fmt(x.r_base, 2, true)}`, `${fmt(x.edge_r, 2, true)} ${ciR(x.edge_ci95)}`, x.conclusion]), "aucune source")),
      card("Signaux trouvés par les stratégies de CSI (shadow)", el("p", { class: "muted small", text: generated.note || "" }),
        table(["Créé", "Paire", "Stratégie", "Entrée · stop · TP1", "Verdict stratégie", "État"],
          (generated.signals || []).map((x) => [when(x.created_at), pair(x.symbol), x.strategy, `${price(x.entry)} · ${price(x.stop_loss)} · ${price(x.targets[0])}`,
            x.strategy_verdict || "–", x.expired ? "expiré" : "actif"]), "aucun signal trouvé")));
  } catch (error) {
    showError(target, error);
  }
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

const ADMISSION_LABELS = { AJOUTEE: ["ok", "ajoutée"], REFUSEE: ["bad", "refusée"], INDISPONIBLE: ["muted", "indisponible sur Binance"], A_DECIDER: ["warn", "à décider"] };

function admissionsCard(data) {
  if (!data) return card("Univers : avis halal", el("p", { class: "muted small", text: "indisponible" }));
  const run = el("button", { type: "button", class: "primary", text: "Appliquer le screening (ajouter les favorables)" });
  run.addEventListener("click", () => runAdmissions(run));
  const pending = (data.pending || []).map((p) => {
    const add = el("button", { type: "button", class: "primary", text: "Ajouter" });
    const refuse = el("button", { type: "button", class: "ghost", text: "Refuser" });
    add.addEventListener("click", () => decideAdmission(p.symbol, "add", add));
    refuse.addEventListener("click", () => decideAdmission(p.symbol, "refuse", refuse));
    return [{ node: el("strong", { text: pair(p.symbol) }) }, p.screening.toLowerCase(), p.reason, { node: el("div", { class: "row" }, add, refuse) }];
  });
  const decisions = (data.decisions || []).map((d) => {
    const [kind, label] = ADMISSION_LABELS[d.decision] || ["muted", d.decision];
    return [pair(d.symbol), { node: el("span", { class: `pill ${kind}`, text: label }) }, d.decided_by, d.reason, when(d.decided_at)];
  });
  return card("Univers : avis halal et décisions d'ajout",
    el("p", { class: "muted small", text: data.rule }),
    el("div", { class: "row" }, run),
    el("h3", { text: `Cryptos à décider (${pending.length})` }),
    table(["Paire", "Avis", "Motif", "Ta décision"], pending, "rien à décider"),
    el("h3", { text: "Décisions" }),
    table(["Paire", "Décision", "Par", "Motif", "Date"], decisions, "aucune décision pour l'instant"));
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
}

function start() {
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => openTab(tab.dataset.tab));
  document.getElementById("analyze").addEventListener("click", analyzePair);
  document.getElementById("market-run").addEventListener("click", runMarket);
  document.getElementById("opportunities-run").addEventListener("click", runOpportunities);
  document.getElementById("evaluate").addEventListener("click", evaluateSignal);
  document.getElementById("token-save").addEventListener("click", () => {
    try { localStorage.setItem(TOKEN_KEY, document.getElementById("token").value.trim()); } catch (_err) { /* stockage bloqué */ }
    document.getElementById("token-box").classList.add("hidden");
    boot();
  });
  document.getElementById("admissions-badge").addEventListener("click", () => openTab("follow"));
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
