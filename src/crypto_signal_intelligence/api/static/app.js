"use strict";
// Tableau de bord interactif de CSI. Analyse seulement : aucun ordre, rien n'est envoyé à BinanceSpotManager.
// Tout texte reçu est inséré avec textContent, jamais comme du HTML.

const TOKEN_KEY = "csi_api_token";
const PREFS_KEY = "csi_dashboard_prefs";
const PARIS = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" });
const state = { horizon: "24h", pairs: [], followLoaded: false };
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
  segmented.replaceChildren(...data.horizons.map((h) => el("button", {
    type: "button", "data-key": h.key, class: h.key === state.horizon ? "on" : "", text: h.label,
    onclick: () => {
      state.horizon = h.key;
      for (const b of segmented.children) b.classList.toggle("on", b.dataset.key === h.key);
    },
  })));
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
  target.replaceChildren(head, el("div", { class: "grid" }, contextCard(r), planCard(r)), overviewCard(r),
    strategiesCard(r), definitionsCard(r));
}

function contextCard(r) {
  const c = r.context || {};
  return card("Situation actuelle",
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
  if (/^(VALIDATED|USEFUL_OOS|SYSTEME_ADMISSIBLE)/.test(v) || /^[1-9]\d* CONDITION/.test(v)) return "ok";
  if (/REJECTED|NOT_USEFUL|AUCUN|ÉCHEC|INCONCLUSIVE/.test(v)) return "bad";
  return "muted";
}

async function loadFollow(force = false) {
  const target = document.getElementById("follow-result");
  if (state.followLoaded && !force) return;
  busy(target, "Chargement…");
  try {
    const [health, models, recent, sources, generated, universe] = await Promise.all([
      api("/health"), api("/models"), api("/signals/recent?limit=15"), api("/sources"), api("/signals/generated?limit=10"), api("/universe"),
    ]);
    state.followLoaded = true;
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
          { node: el("strong", { class: verdictClass(m.verdict), text: m.verdict || m.status }) }, when(m.created_at), m.source]),
        "aucun modèle évalué")),
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
  document.getElementById("evaluate").addEventListener("click", evaluateSignal);
  document.getElementById("token-save").addEventListener("click", () => {
    try { localStorage.setItem(TOKEN_KEY, document.getElementById("token").value.trim()); } catch (_err) { /* stockage bloqué */ }
    document.getElementById("token-box").classList.add("hidden");
    boot();
  });
  boot();
  setInterval(refreshHealth, 60000);
}

async function boot() {
  refreshHealth();
  const params = new URLSearchParams(window.location.search);
  const tab = { signal: "signal", suivi: "follow" }[params.get("onglet")];
  if (tab) openTab(tab);
  try {
    await loadPairs();
  } catch (error) {
    showError(document.getElementById("pair-result"), error);
    return;
  }
  const wanted = (params.get("paire") || "").toUpperCase().replace("/", "");
  if (wanted && state.pairs.some((p) => p.symbol === wanted)) {
    document.getElementById("pair").value = wanted;
    const horizon = params.get("horizon");
    if (horizon) {
      for (const b of document.getElementById("horizons").children) {
        if (b.dataset.key === horizon) { state.horizon = horizon; }
        b.classList.toggle("on", b.dataset.key === state.horizon);
      }
    }
    analyzePair();
  }
}

document.addEventListener("DOMContentLoaded", start);
