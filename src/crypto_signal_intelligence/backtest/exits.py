"""Politiques de sortie et traitement d'une bougie pour une position longue simulée.

Conventions (partagées avec backtest/simulator.py) :
- stop (stop-market) : déclenché si low <= stop ; ouverture sous le stop = gap, exécution à
  l'ouverture moins glissement, jamais « au prix du stop » ;
- TP_k (limite vendeuse pour la fraction weight_k de la quantité initiale) : rempli si
  high > TP_k strictement, au prix du TP ; ouverture au-dessus d'un TP : au prix du TP, jamais mieux ;
- stop et TP dans la même bougie : ordre inconnu → stop sur le restant, TP non rempli
  (pessimiste) ; la borne optimiste suppose les TP touchés remplis puis le restant sorti au
  stop ; le cas est compté AMBIGUOUS ;
- bougie de remplissage « au contact » : aucun TP dans cette bougie (le plus haut a pu
  précéder l'entrée), seule la borne optimiste les compte ;
- un stop remonté après un TP s'applique à partir de la bougie SUIVANTE : aucun déplacement
  rétroactif au bénéfice du backtest ;
- sortie temporelle (profil théorique) au close après la durée maximale, si la politique
  la prévoit ; un consommateur sans sortie temporelle garde la position jusqu'au TP ou au stop.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

STOP_FIXED = "FIXED"
STOP_BREAK_EVEN = "BREAK_EVEN_AFTER_TP1"
# Break-even au prix moyen d'achat RÉEL après le premier TP rempli (BSM) ; avec une seule entrée
# simulée, ce prix moyen est le prix de remplissage : même trajectoire que STOP_BREAK_EVEN.
STOP_BREAK_EVEN_AVG_FILL = "BREAK_EVEN_AVG_FILL_AFTER_FIRST_TP"
STOP_TRAIL_PREVIOUS_TP = "TRAIL_PREVIOUS_TP"
STOP_RULES = (STOP_FIXED, STOP_BREAK_EVEN, STOP_BREAK_EVEN_AVG_FILL, STOP_TRAIL_PREVIOUS_TP)
# TP en ordre limite au repos (rempli si high > TP, au prix du TP) ou vente AU MARCHÉ sur
# déclenchement (BinanceSpotManager : dernier prix >= TP, puis ordre marché → TP moins glissement).
TP_LIMIT = "LIMIT"
TP_MARKET_ON_TRIGGER = "MARKET_ON_TRIGGER"
STOP_MARKET = "STOP_MARKET"
STOP_LIMIT = "STOP_LIMIT"
# Stop-limit ; si la plateforme le refuse parce que le prix l'a déjà franchi : vente au marché.
STOP_LIMIT_MARKET_IF_CROSSED = "STOP_LIMIT_MARKET_IF_CROSSED"
POLICY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ExitPolicy:
    """Politique de gestion PARTAGÉE entre le backtest et l'exécuteur (docs/EXIT_POLICIES.md).

    Tous les champs sauf `description` entrent dans l'empreinte `policy_hash()` : deux
    politiques de même identifiant mais de règles différentes ont des empreintes différentes.
    """

    policy_id: str
    stop_rule: str = STOP_FIXED
    time_exit: bool = True
    tp_execution: str = TP_LIMIT
    description: str = ""
    # Ordre de stop côté exécuteur ; le simulateur l'approxime en stop-market (écart documenté).
    stop_order: str = STOP_MARKET
    stop_limit_offset_bps: int = 0
    # Un stop remonté s'applique après la confirmation du TP qui le déclenche ; en simulation,
    # à partir de la bougie suivante (jamais dans la bougie du TP).
    stop_move_applies: str = "AFTER_TP_FILL_CONFIRMED"
    # Poids des TP : fractions de la quantité d'entrée RÉELLEMENT remplie.
    tp_weight_basis: str = "FILLED_BASE_QUANTITY"
    # Reliquats : le dernier TP vend tout le restant ; une tranche sous les minimums de la
    # plateforme est reportée sur le TP suivant.
    remainder_rule: str = "LAST_TP_SELLS_REMAINDER"
    below_minimum_rule: str = "MERGE_INTO_NEXT_TP"
    # Entrées : les ordres non remplis sont annulés à ENTRY_EXPIRES_AT ou dès le premier TP.
    unfilled_entries_rule: str = "CANCEL_AT_ENTRY_EXPIRY_OR_FIRST_TP"
    max_entries: int = 1

    def canonical(self) -> dict:
        data = asdict(self)
        data.pop("description")
        data["schema_version"] = POLICY_SCHEMA_VERSION
        return data

    def policy_hash(self) -> str:
        """Empreinte SHA-256 (16 premiers caractères hexadécimaux) du JSON canonique."""
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


def _bsm(policy_id: str, stop_rule: str, description: str) -> ExitPolicy:
    return ExitPolicy(policy_id, stop_rule, time_exit=False, tp_execution=TP_MARKET_ON_TRIGGER, description=description,
                      stop_order=STOP_LIMIT, stop_limit_offset_bps=30)


def _bsm_v2(policy_id: str, stop_rule: str, description: str) -> ExitPolicy:
    """Comportement RÉEL de BinanceSpotManager (relevé dans son code le 2026-09-30, branche feat/csi-v2-drop) :
    poids appliqués à la quantité nette (frais payés en actif de base déduits), stop-limit −30 pb avec vente
    au marché si le stop est déjà franchi, break-even au prix moyen d'achat réel."""
    return ExitPolicy(policy_id, stop_rule, time_exit=False, tp_execution=TP_MARKET_ON_TRIGGER, description=description,
                      stop_order=STOP_LIMIT_MARKET_IF_CROSSED, stop_limit_offset_bps=30,
                      tp_weight_basis="NET_FILLED_BASE_QUANTITY")


EXIT_POLICIES: dict[str, ExitPolicy] = {
    "FIXED_SL_ONE_TP_V1": ExitPolicy("FIXED_SL_ONE_TP_V1", description="stop fixe, un TP limite, sortie temporelle"),
    "FIXED_SL_FOUR_TP_V1": ExitPolicy("FIXED_SL_FOUR_TP_V1", description="stop fixe, TP partiels limite, sortie temporelle"),
    "BREAK_EVEN_AFTER_TP1_V1": ExitPolicy("BREAK_EVEN_AFTER_TP1_V1", STOP_BREAK_EVEN,
                                          description="stop remonté au prix d'entrée après TP1, TP partiels limite"),
    "TRAIL_PREVIOUS_TP_V1": ExitPolicy("TRAIL_PREVIOUS_TP_V1", STOP_TRAIL_PREVIOUS_TP,
                                       description="stop remonté au TP précédent après chaque TP"),
    # Profil observé de BinanceSpotManager (docs/BSM_PROFILE.md) : TP au marché sur déclenchement,
    # stop STOP_LOSS_LIMIT (limite = stop − 0,3 %), fixe (branche main) ou remonté à l'entrée après
    # TP1 (préférence), aucune sortie temporelle.
    # V1 : première description, inexacte sur trois points (voir V2) ; conservée pour les expériences passées.
    "BSM_MARKET_TP_FIXED_SL_V1": _bsm("BSM_MARKET_TP_FIXED_SL_V1", STOP_FIXED,
                                      "BinanceSpotManager (V1, remplacée par V2) : TP au marché, stop fixe"),
    "BSM_MARKET_TP_BREAK_EVEN_V1": _bsm("BSM_MARKET_TP_BREAK_EVEN_V1", STOP_BREAK_EVEN,
                                        "BinanceSpotManager (V1, remplacée par V2) : TP au marché, stop à l'entrée"),
    "BSM_MARKET_TP_FIXED_SL_V2": _bsm_v2("BSM_MARKET_TP_FIXED_SL_V2", STOP_FIXED,
                                         "BinanceSpotManager : TP au marché, stop fixe, sans sortie temporelle"),
    "BSM_MARKET_TP_BREAK_EVEN_V2": _bsm_v2("BSM_MARKET_TP_BREAK_EVEN_V2", STOP_BREAK_EVEN_AVG_FILL,
                                           "BinanceSpotManager : TP au marché, stop au prix moyen réel après le 1er TP"),
}


def exit_policy(policy_id: str) -> ExitPolicy:
    try:
        return EXIT_POLICIES[policy_id]
    except KeyError:
        raise ValueError(f"politique de sortie inconnue : {policy_id} ; connues : {sorted(EXIT_POLICIES)}") from None


def policies_document() -> dict:
    """Registre exportable (config/exit_policies.json) : l'exécuteur doit reconnaître id ET empreinte."""
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "hash_algorithm": "sha256(json canonique, clés triées, sans espaces)[:16]",
        "policies": {pid: {"hash": p.policy_hash(), "description": p.description, "rules": p.canonical()}
                     for pid, p in sorted(EXIT_POLICIES.items())},
    }


@dataclass(frozen=True)
class Fill:
    reason: str            # TP | SL | SL_GAP | TIMEOUT | CENSORED
    price: float
    weight: float          # fraction de la quantité initiale
    target_index: int | None = None

    def to_dict(self) -> dict:
        return {"reason": self.reason, "price": self.price, "weight": self.weight, "target": self.target_index}


@dataclass
class OpenPosition:
    entry: float
    initial_stop: float
    targets: tuple[float, ...]
    weights: tuple[float, ...]
    policy: ExitPolicy
    stop: float = field(init=False)
    pending_stop: float | None = None
    next_target: int = 0
    remaining: float = 1.0
    fills: list[Fill] = field(default_factory=list)
    optimistic_fills: list[Fill] | None = None
    ambiguous: bool = False
    low: float = float("inf")
    high: float = float("-inf")

    def __post_init__(self) -> None:
        if not 0 < self.initial_stop < self.entry:
            raise ValueError("stop initial sous l'entrée requis")
        if len(self.targets) != len(self.weights) or any(t <= self.entry for t in self.targets) or \
                list(self.targets) != sorted(self.targets) or abs(sum(self.weights) - 1) > 1e-9:
            raise ValueError("TP strictement croissants au-dessus de l'entrée, poids sommant à 1")
        self.stop = self.initial_stop

    @property
    def closed(self) -> bool:
        return self.remaining <= 1e-12

    @property
    def risk(self) -> float:
        return self.entry - self.initial_stop

    @property
    def exit_reason(self) -> str | None:
        return self.fills[-1].reason if self.closed and self.fills else None

    @property
    def exit_price(self) -> float | None:
        return self.fills[-1].price if self.fills else None

    def process_bar(self, o: float, h: float, low: float, c: float, *, first_bar: bool, touched: bool,
                    market_cost: float, time_limit_reached: bool) -> None:
        """Applique une bougie clôturée ; peut fermer la position (`closed`)."""
        if self.pending_stop is not None:
            self.stop = max(self.stop, self.pending_stop)
            self.pending_stop = None
        if not touched:
            self.high = max(self.high, h)
        self.low = min(self.low, low)
        if not first_bar:
            if o <= self.stop:
                self._close("SL_GAP", o * (1 - market_cost))
                return
            while self.next_target < len(self.targets) and self._reached(o, self.targets[self.next_target]):
                self._take(self.next_target, market_cost)
            if self.closed:
                return
        sl_hit = low <= self.stop
        hit = [k for k in range(self.next_target, len(self.targets)) if self._reached(h, self.targets[k])]
        if hit and (touched or sl_hit):
            self.ambiguous = True
            if self.optimistic_fills is None:
                self.optimistic_fills = self._optimistic(hit, sl_hit, market_cost, c)
            hit = []
        if sl_hit:
            self._close("SL", self.stop * (1 - market_cost))
            return
        for k in hit:
            self._take(k, market_cost)
        if self.closed:
            return
        if time_limit_reached and self.policy.time_exit:
            self._close("TIMEOUT", c * (1 - market_cost))

    def censor(self, price: float) -> None:
        self._close("CENSORED", price)

    def _reached(self, price: float, target: float) -> bool:
        """Limite au repos : dépassement strict requis ; marché sur déclenchement : contact suffit."""
        return price >= target if self.policy.tp_execution == TP_MARKET_ON_TRIGGER else price > target

    def _tp_price(self, k: int, market_cost: float) -> float:
        target = self.targets[k]
        return target * (1 - market_cost) if self.policy.tp_execution == TP_MARKET_ON_TRIGGER else target

    def _take(self, k: int, market_cost: float) -> None:
        weight = min(self.weights[k], self.remaining)
        self.fills.append(Fill("TP", self._tp_price(k, market_cost), weight, k + 1))
        self.remaining -= weight
        self.next_target = k + 1
        self._raise_stop_after(k)

    def _raise_stop_after(self, k: int) -> None:
        rule = self.policy.stop_rule
        if rule in (STOP_BREAK_EVEN, STOP_BREAK_EVEN_AVG_FILL) and k == 0:
            new_stop = self.entry
        elif rule == STOP_TRAIL_PREVIOUS_TP:
            new_stop = self.entry if k == 0 else self.targets[k - 1]
        else:
            return
        if new_stop > self.stop:
            self.pending_stop = max(self.pending_stop or new_stop, new_stop)  # dès la bougie suivante

    def _close(self, reason: str, price: float) -> None:
        if not self.closed:
            self.fills.append(Fill(reason, price, self.remaining))
            self.remaining = 0.0

    def _optimistic(self, hit: list[int], sl_hit: bool, market_cost: float, close: float) -> list[Fill]:
        """Borne optimiste : TP touchés remplis dans cette bougie, restant au stop (ou au close)."""
        fills, remaining = list(self.fills), self.remaining
        for k in hit:
            weight = min(self.weights[k], remaining)
            fills.append(Fill("TP", self._tp_price(k, market_cost), weight, k + 1))
            remaining -= weight
        if remaining > 1e-12:
            fills.append(Fill("SL", self.stop * (1 - market_cost), remaining) if sl_hit
                         else Fill("TIMEOUT", close * (1 - market_cost), remaining))
        return fills


def pnl_per_unit(fills: list[Fill], entry: float, fee: float) -> float:
    """PnL net par unité de quantité initiale : frais sur l'achat et sur chaque vente."""
    return sum(f.weight * (f.price * (1 - fee) - entry * (1 + fee)) for f in fills)


def gross_return(fills: list[Fill], entry: float) -> float:
    return sum(f.weight * (f.price / entry - 1) for f in fills)
