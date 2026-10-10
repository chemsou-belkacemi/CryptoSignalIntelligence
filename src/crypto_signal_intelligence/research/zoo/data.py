"""Données du zoo (docs/ZOO.md § 2 et § 3) : lectures coupées, agrégation datée, univers à date, séries externes.

Règles appliquées ici, une fois pour tout le moteur :
- **coupure dure** : aucune bougie dont la clôture dépasse le 2025-07-01 00:00 UTC (fin de DEVELOPMENT,
  `research/protocol.FROZEN_DEVELOPMENT_END`), aucune valeur externe ni aucun événement après cette borne ; une borne
  plus tardive est refusée (`LectureRefusee`) AVANT toute lecture. La découverte lit jusqu'au 2023-01-01 00:00 seulement ;
- **agrégation** 4 h et journalière alignée sur 00:00 UTC, blocs COMPLETS seulement (4 ou 24 bougies 1 h), datée par
  l'`available_at` de sa DERNIÈRE bougie 1 h ;
- **univers à date** : top 40 du mois de l'instant de décision (`research/pit_universe.membership`, calculé au 1er du
  mois sur les 30 journées qui finissent la veille : rien de postérieur au mois n'est lu), paires retirées comprises ;
- **séries externes** jointes « au plus tard » sur `available_at` STRICTEMENT antérieur à la décision.

Temps : heures entières depuis 1970 (`h`), une bougie 1 h ouverte à `h` se clôt à `h + 1` ; journée UTC `d` = heures
`[24 d ; 24 d + 24]`. Décisions : instants `available_at` en nanosecondes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ...config import Settings
from ..protocol import FROZEN_DEVELOPMENT_END, development_end

HOUR_NS = 3_600_000_000_000
DAY_H = 24
#: Borne EXCLUSIVE de lecture : clôtures ≤ 2025-07-01 00:00 UTC (la bougie 23:00-24:00 du 30 juin est la dernière lue).
HARD_END = pd.Timestamp(FROZEN_DEVELOPMENT_END).floor("s") + pd.Timedelta(seconds=1)
LATENCE_NS = 2_000_000_000          # 2 s, `data.assumed_availability_latency_seconds` du magasin (fixé ici)
UNITES = {"1h": 1, "4h": 4, "1j": 24}
NOMS_UNITES = {v: k for k, v in UNITES.items()}
COLONNES_1H = ("open_time", "open", "high", "low", "close", "quote_volume", "taker_buy_quote_volume",
               "number_of_trades", "available_at")


def en_ns(values) -> np.ndarray:
    """Instants UTC en nanosecondes (int64), quelle que soit la résolution stockée (ns, µs, ms)."""
    return pd.to_datetime(pd.Series(values), utc=True).dt.as_unit("ns").astype("int64").to_numpy()


class LectureRefusee(PermissionError):
    """Lecture au-delà de la borne permise : refusée avant toute lecture."""


# --- Étapes (§ 3) -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Etape:
    nom: str
    debut: pd.Timestamp          # première décision permise
    fin: pd.Timestamp            # dernière décision permise (23:59:59)
    annees: tuple[str, ...]      # années (ou demi-année) des garde-fous

    @property
    def debut_ns(self) -> int:
        return int(self.debut.value)

    @property
    def fin_ns(self) -> int:
        return int(self.fin.value)

    @property
    def fin_exclusive(self) -> pd.Timestamp:
        """Borne de lecture de l'étape : dernière clôture de l'étape (minuit suivant sa dernière journée)."""
        return self.fin.floor("s") + pd.Timedelta(seconds=1)

    @property
    def fin_h(self) -> int:
        """Heure de la dernière clôture de l'étape : une position encore ouverte y est vendue."""
        return int(self.fin_exclusive.value // HOUR_NS)

    @property
    def debut_jour(self) -> int:
        return int(self.debut.value // HOUR_NS) // DAY_H

    @property
    def fin_jour(self) -> int:
        """Dernière journée UTC de l'étape (incluse)."""
        return self.fin_h // DAY_H - 1

    def jours(self) -> np.ndarray:
        return np.arange(self.debut_jour, self.fin_jour + 1, dtype=np.int64)


DECOUVERTE = Etape("decouverte", pd.Timestamp("2019-01-01", tz="UTC"),
                   pd.Timestamp("2022-12-31 23:59:59", tz="UTC"), ("2019", "2020", "2021", "2022"))
VALIDATION = Etape("validation", pd.Timestamp("2023-01-01", tz="UTC"),
                   pd.Timestamp("2025-06-30 23:59:59", tz="UTC"), ("2023", "2024", "2025"))
ETAPES = {e.nom: e for e in (DECOUVERTE, VALIDATION)}


def annee_du_jour(jours: np.ndarray) -> np.ndarray:
    """Année civile (entier) de chaque journée UTC."""
    return np.asarray(jours, dtype="int64").astype("datetime64[D]").astype("datetime64[Y]").astype(np.int64) + 1970


def borne_lecture(fin_exclusive: pd.Timestamp, settings: Settings | None = None) -> pd.Timestamp:
    """Borne exclusive de lecture, refusée si elle dépasse le 2025-07-01 00:00 UTC (ou la fin de DEVELOPMENT
    déclarée par la configuration, qui ne peut être que plus tôt : le zoo exige alors une fin identique)."""
    fin = pd.Timestamp(fin_exclusive)
    fin = fin.tz_localize("UTC") if fin.tzinfo is None else fin.tz_convert("UTC")
    if fin > HARD_END:
        raise LectureRefusee(f"lecture jusqu'au {fin} refusée : borne figée {HARD_END} (fin de DEVELOPMENT)")
    if settings is not None:
        declared = pd.Timestamp(development_end(settings)).floor("s") + pd.Timedelta(seconds=1)
        if declared < fin:
            raise LectureRefusee(f"fin de DEVELOPMENT de la configuration ({declared}) antérieure à la lecture "
                                 f"demandée ({fin}) : configuration incompatible avec docs/ZOO.md")
    return fin


# --- Bougies -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Bougies:
    """Bougies CLOSES d'une unité (1, 4 ou 24 heures), en tableaux numpy. `disponible` : instant de décision (ns)."""
    unite: int
    ouverture_h: np.ndarray
    disponible: np.ndarray
    ouverture: np.ndarray
    haut: np.ndarray
    bas: np.ndarray
    cloture: np.ndarray
    volume: np.ndarray          # volume en devise de cotation (USDT)
    achat: np.ndarray           # volume acheteur au marché, en devise de cotation
    transactions: np.ndarray

    def __len__(self) -> int:
        return len(self.cloture)

    @property
    def cloture_h(self) -> np.ndarray:
        return self.ouverture_h + self.unite

    def tete(self, n: int) -> Bougies:
        """Les `n` premières bougies (recalcul sur données tronquées)."""
        return Bougies(self.unite, *(getattr(self, f)[:n] for f in _CHAMPS))

    def filtre(self, garde: np.ndarray) -> Bougies:
        return Bougies(self.unite, *(getattr(self, f)[garde] for f in _CHAMPS))


_CHAMPS = ("ouverture_h", "disponible", "ouverture", "haut", "bas", "cloture", "volume", "achat", "transactions")


def bougies_1h(frame: pd.DataFrame) -> Bougies:
    """Bougies 1 h canoniques (magasin long) → tableaux, triées, doublons écartés."""
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    open_ns = en_ns(frame["open_time"])
    avail = en_ns(frame["available_at"])
    if (open_ns % HOUR_NS).any():
        raise ValueError("bougies 1 h non alignées sur l'heure")

    def col(name: str) -> np.ndarray:
        return frame[name].to_numpy(np.float64)

    return Bougies(1, (open_ns // HOUR_NS).astype(np.int64), avail.astype(np.int64), col("open"), col("high"),
                   col("low"), col("close"), col("quote_volume"), col("taker_buy_quote_volume"),
                   col("number_of_trades"))


def agreger(h1: Bougies, unite: int) -> Bougies:
    """Bougies de `unite` heures alignées sur 00:00 UTC, COMPLÈTES seulement, datées par l'`available_at` de leur
    dernière bougie 1 h. Ouverture de la 1re, plus haut / plus bas extrêmes, clôture de la dernière, volumes sommés."""
    if unite == 1:
        return h1
    if h1.unite != 1 or DAY_H % unite:
        raise ValueError("agrégation depuis des bougies 1 h vers 4 h ou 24 h seulement")
    block = h1.ouverture_h // unite
    if len(block) == 0:
        return Bougies(unite, *(np.empty(0, dtype=getattr(h1, f).dtype) for f in _CHAMPS))
    starts = np.flatnonzero(np.r_[True, block[1:] != block[:-1]])
    counts = np.diff(np.r_[starts, len(block)])
    ends = starts + counts - 1
    full = counts == unite
    s, e = starts[full], ends[full]
    return Bougies(unite, block[s] * unite, h1.disponible[e], h1.ouverture[s],
                   np.maximum.reduceat(h1.haut, starts)[full], np.minimum.reduceat(h1.bas, starts)[full],
                   h1.cloture[e], np.add.reduceat(h1.volume, starts)[full], np.add.reduceat(h1.achat, starts)[full],
                   np.add.reduceat(h1.transactions, starts)[full])


@dataclass
class Grille:
    """Clôtures 1 h d'une paire sur la grille des heures : `existe` (une bougie se clôt à cette heure), `prix`
    (dernière clôture connue, propagée dans les trous ; NaN avant la cotation)."""
    debut_h: int
    existe: np.ndarray
    prix: np.ndarray

    @classmethod
    def depuis(cls, h1: Bougies) -> Grille:
        close_h = h1.cloture_h
        if len(close_h) == 0:
            return cls(0, np.zeros(0, bool), np.zeros(0))
        first, last = int(close_h[0]), int(close_h[-1])
        exists = np.zeros(last - first + 1, dtype=bool)
        raw = np.full(last - first + 1, np.nan)
        exists[close_h - first] = True
        raw[close_h - first] = h1.cloture
        idx = np.where(exists, np.arange(len(raw)), 0)
        np.maximum.accumulate(idx, out=idx)
        return cls(first, exists, raw[idx])

    @property
    def fin_h(self) -> int:
        return self.debut_h + len(self.existe) - 1


@dataclass
class Paire:
    """Une paire : bougies 1 h coupées, grille des clôtures, agrégations en cache."""
    symbole: str
    h1: Bougies
    grille: Grille = field(init=False)
    _cache: dict[int, Bougies] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.grille = Grille.depuis(self.h1)

    def unite(self, heures: int) -> Bougies:
        if heures not in self._cache:
            self._cache[heures] = agreger(self.h1, heures)
        return self._cache[heures]

    def empreinte(self) -> str:
        digest = hashlib.sha256()
        for name in ("ouverture_h", "disponible", "ouverture", "haut", "bas", "cloture", "volume", "achat"):
            digest.update(np.ascontiguousarray(getattr(self.h1, name)).tobytes())
        return digest.hexdigest()


def couper(h1: Bougies, fin_exclusive: pd.Timestamp) -> Bougies:
    """Seules les bougies 1 h CLOSES au plus tard à la borne (et connues au plus tard à la borne + latence)."""
    fin_h = int(pd.Timestamp(fin_exclusive).value // HOUR_NS)
    return h1.filtre(h1.cloture_h <= fin_h)


def charger_paire(settings: Settings, symbole: str, fin_exclusive: pd.Timestamp) -> Paire | None:
    """Bougies 1 h du magasin long, coupées à la borne (refusée au-delà du 2025-07-01 00:00). None si absente."""
    from ...features.loader import MissingData
    from ..long_history import load_long
    fin = borne_lecture(fin_exclusive, settings)
    try:
        frame = load_long(settings, symbole)
    except MissingData:
        return None
    frame = frame[pd.to_datetime(frame["open_time"], utc=True) + pd.Timedelta(hours=1) <= fin]
    if frame.empty:
        return None
    return Paire(symbole, couper(bougies_1h(frame[list(COLONNES_1H)]), fin))


# --- Univers à date ------------------------------------------------------------------------------------------------

def mois_de(ns: np.ndarray) -> np.ndarray:
    """Mois (depuis 1970-01) de chaque instant en nanosecondes."""
    return np.asarray(ns, dtype=np.int64).astype("datetime64[ns]").astype("datetime64[M]").astype(np.int64)


def mois_du_jour(jours: np.ndarray) -> np.ndarray:
    return np.asarray(jours, dtype=np.int64).astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)


@dataclass
class Univers:
    """Top 40 à date : mois (depuis 1970-01) → paires membres."""
    par_mois: dict[int, frozenset[str]]

    @classmethod
    def depuis(cls, membership: pd.DataFrame, fin_exclusive: pd.Timestamp | None = None) -> Univers:
        frame = membership
        if fin_exclusive is not None:
            frame = frame[pd.to_datetime(frame["month"], utc=True) < pd.Timestamp(fin_exclusive)]
        months = mois_de(en_ns(frame["month"]))
        out: dict[int, set[str]] = {}
        for month, symbol in zip(months, frame["symbol"], strict=True):
            out.setdefault(int(month), set()).add(str(symbol))
        return cls({k: frozenset(v) for k, v in out.items()})

    def symboles(self) -> list[str]:
        return sorted(set().union(*self.par_mois.values())) if self.par_mois else []

    def membre(self, symbole: str, instants_ns: np.ndarray) -> np.ndarray:
        """Appartenance au top du mois de chaque instant de décision."""
        months = mois_de(instants_ns)
        return np.array([symbole in self.par_mois.get(int(m), ()) for m in months], dtype=bool) if len(months) \
            else np.zeros(0, dtype=bool)

    def membre_jours(self, symbole: str, jours: np.ndarray) -> np.ndarray:
        months = mois_du_jour(jours)
        return np.array([symbole in self.par_mois.get(int(m), ()) for m in months], dtype=bool) if len(months) \
            else np.zeros(0, dtype=bool)

    def membres_du_mois(self, mois: int) -> frozenset[str]:
        return self.par_mois.get(int(mois), frozenset())


def charger_univers(settings: Settings, fin_exclusive: pd.Timestamp) -> Univers:
    from ..pit_universe import load_membership
    return Univers.depuis(load_membership(settings), borne_lecture(fin_exclusive, settings))


def recensement(settings: Settings) -> pd.DataFrame:
    """Recensement des paires USDT (`data/pit/census.csv`), exclusions structurelles et haram comprises."""
    from ..pit_universe import pit_dir
    return pd.read_csv(pit_dir(settings) / "census.csv")


def eligibles_f31(census: pd.DataFrame) -> frozenset[str]:
    """Paires éligibles à F31 (descriptif, § 10) : admises (FAVORABLE) et non exclues ; les NON_RELEVE n'en sont pas."""
    kept = census[census["excluded"].isna() & (census["halal_status"] == "FAVORABLE")]
    return frozenset(kept["symbol"].astype(str))


def charger_quotidien_recensement(settings: Settings, fin_exclusive: pd.Timestamp) -> pd.DataFrame:
    """Clôtures journalières de toutes les paires USDT retenues du recensement (`data/pit/daily.parquet`), univers
    d'exception d'EV02 : journées closes au plus tard à la borne."""
    from ..pit_universe import pit_dir
    fin = borne_lecture(fin_exclusive, settings)
    daily = pd.read_parquet(pit_dir(settings) / "daily.parquet")
    daily = daily[pd.to_datetime(daily["day"], utc=True) + pd.Timedelta(days=1) <= fin]
    kept = set(recensement(settings).query("excluded != excluded")["symbol"])
    return daily[daily["symbol"].isin(kept)].reset_index(drop=True)


# --- Séries et événements externes -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class SerieExterne:
    nom: str
    disponible: np.ndarray       # ns, triés
    date: np.ndarray             # ns
    valeur: np.ndarray

    def __len__(self) -> int:
        return len(self.valeur)


def serie_depuis(frame: pd.DataFrame, nom: str, fin_exclusive: pd.Timestamp) -> SerieExterne:
    """Lignes (date, value, available_at) → série coupée : date ET `available_at` antérieures à la borne."""
    fin = borne_lecture(fin_exclusive)
    frame = frame.assign(date=pd.to_datetime(frame["date"], utc=True),
                         available_at=pd.to_datetime(frame["available_at"], utc=True))
    frame = frame[(frame["date"] < fin) & (frame["available_at"] < fin)].sort_values(["available_at", "date"])
    return SerieExterne(nom, en_ns(frame["available_at"]), en_ns(frame["date"]),
                        frame["value"].to_numpy(np.float64))


def charger_serie(settings: Settings, serie: str, cle: str, fin_exclusive: pd.Timestamp,
                  champ: str = "value") -> SerieExterne:
    """Série du magasin de contexte (lignes HISTORIQUE), coupée à la borne dès la lecture."""
    from ...context.store import HISTORY, load
    borne_lecture(fin_exclusive, settings)
    frame = load(settings, serie)
    frame = frame[(frame["kind"] == HISTORY) & (frame["key"] == cle) & (frame["field"] == champ)]
    if "available_at" not in frame:
        raise ValueError(f"{serie}:{cle} sans `available_at` : jointure causale impossible")
    return serie_depuis(frame, f"{serie}:{cle}", fin_exclusive)


def joindre(decisions_ns: np.ndarray, serie: SerieExterne) -> np.ndarray:
    """Dernière valeur dont l'`available_at` est STRICTEMENT antérieur à chaque décision (NaN sinon). Les marchés
    fermés le week-end : la valeur du vendredi est reprise le samedi et le dimanche."""
    idx = np.searchsorted(serie.disponible, np.asarray(decisions_ns, dtype=np.int64), side="left") - 1
    out = np.full(len(idx), np.nan)
    ok = idx >= 0
    out[ok] = serie.valeur[idx[ok]]
    return out


def charger_evenements(settings: Settings, nom: str, fin_exclusive: pd.Timestamp) -> pd.DataFrame:
    """Table d'événements du magasin de contexte, coupée : événements antérieurs à la borne seulement."""
    from ...context.zoo import load_events
    fin = borne_lecture(fin_exclusive, settings)
    frame = load_events(settings, nom)
    if frame.empty:
        return frame
    times = pd.to_datetime(frame["event_time"], utc=True)
    return frame[times < fin].reset_index(drop=True)


def empreinte_fichier(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
