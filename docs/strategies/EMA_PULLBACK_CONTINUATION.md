# Fiche d'hypothèse — B. EMA_PULLBACK_CONTINUATION (v1)

Statut : **RESEARCH**. Rédigée le 2026-09-30, avant tout backtest de cette stratégie. Paramètres = points de départ expérimentaux, ni optimaux ni réputés rentables.

| Rubrique | Contenu |
|---|---|
| Hypothèse | Dans une tendance établie, un repli vers la moyenne courte suivi d'une reprise offre une poursuite du mouvement avec une invalidation proche, donc un risque faible par rapport à la cible. |
| Mécanisme supposé | Acheteurs en retard qui attendent un repli, suivi de tendance. Non vérifiable avec l'OHLCV seul. |
| Régime attendu | Tendance 1h BULL : EMA20 > EMA50, close > EMA50, pente EMA50 sur 3 bougies > 0 (filtre testé par ablation). |
| Univers | BTCUSDT, ETHUSDT (lot 2) ; 16 paires de docs/UNIVERSE.md depuis le 2026-09-30. Aucune généralisation revendiquée. |
| Données | Chandeliers 15m (setup) et 1h (contexte), bougies clôturées uniquement. |
| Règle exacte | zone = EMA20 + 0,25 ATR14 ; EMA20 > EMA50 en 15m ; **reprise** : close(t) > zone(t) et close(t−1) ≤ zone(t−1) ; **contact** : plus bas des 4 dernières bougies (t incluse) ≤ zone(t) ; **profondeur** : ce plus bas ≥ EMA20 − 1,5 ATR14. |
| Entrée | LIMIT à close × (1 + 10 bps), active à la bougie suivante, expire après 2 bougies (expiration rapide si le setup se dégrade). |
| Stop | plus bas du repli − 0,25 ATR14 ; refus si le risque dépasse 3 ATR14. |
| Invalidation avant remplissage | Aucune autre que l'expiration : l'ordre reste au repos jusqu'à ENTRY_EXPIRES_AT. Un remplissage à un prix ≤ stop (ouverture en gap) est suivi d'une vente au marché immédiate (SL_GAP), jamais d'un fill « au stop ». |
| Expirations (TXT V3) | ENTRY_EXPIRES_AT = décision + 2 bougies 15m (fenêtre d'entrée, identique au backtest) ; EXPIRES_AT = création + 15 min (acceptation du message par le consommateur), plafonné à ENTRY_EXPIRES_AT. |
| Nouveau setup | Un seul setup actif par paire et par stratégie. Tout candidat apparu pendant qu'un ordre est au repos ou qu'une position est ouverte est compté DUPLICATE et abandonné (jamais mis en file). Un nouveau setup est possible dès la clôture de la bougie où l'ordre a expiré ou la position s'est fermée ; en direct, le registre refuse aussi un second signal non expiré pour la même paire et stratégie. |
| Sortie | Un TP de référence à 2R (FIXED_SL_ONE_TP_V1) ; sortie au close après 96 bougies (profil théorique). |
| Coûts | Scénarios central / défavorable / stress. Veto si RR net TP1 < 1,2 en coûts centraux. |
| Modes d'échec | Inversion de tendance (le repli est le début d'une baisse), achats répétés pendant une baisse. Un seul setup actif par paire et par stratégie (simulateur et registre de signaux). |
| Plage de paramètres | Grille de recalibrage : zone_atr {0,25 ; 0,5} × stop_buffer_atr {0,25 ; 0,5} × target_r {1,5 ; 2 ; 3} = 12 essais. |
| Comparaisons | Même règle sans filtre de contexte 1h ; v1 sans recalibrage ; buy-and-hold ; cash. |
| Validation | Walk-forward purgé (docs/PROTOCOL.md) ; test final réservé depuis 2025-07-01. |
| Critère d'abandon | Verdict REJECTED (E[R] nette hors échantillon ≤ 0 en coûts centraux), OU résultat porté par une seule année/paire, OU disparition en coûts défavorables. |
