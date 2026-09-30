# Fiche d'hypothèse — A. DONCHIAN_VOLUME_BREAKOUT (v1)

Statut : **RESEARCH**. Paramètres = points de départ expérimentaux, ni optimaux ni réputés rentables.

| Rubrique | Contenu |
|---|---|
| Hypothèse | Une clôture au-dessus d'une consolidation récente, accompagnée d'un volume inhabituel, signale une demande qui peut prolonger le mouvement au-delà des coûts. |
| Mécanisme supposé | Ordres en attente au-dessus des plus hauts, suivi de tendance, rattrapage d'information. Non vérifiable avec l'OHLCV seul. |
| Régime attendu | Tendance 1h BULL (filtre testé par ablation). Volatilité quelconque, analysée a posteriori par régime. |
| Univers | BTCUSDT, ETHUSDT (lots 1 et 2) ; 16 paires de docs/UNIVERSE.md depuis le 2026-09-30. Aucune généralisation revendiquée. |
| Données | Chandeliers 15m (setup) et 1h (contexte), Binance Spot public, bougies clôturées uniquement. |
| Règle exacte | niveau = max(high) des 20 bougies 15m **précédentes** ; setup si close > niveau ; volume > 1,5 × moyenne des 20 volumes précédents ; tendance 1h BULL ; (close − niveau) ≤ 0,5 ATR14. |
| Entrée | Ordre LIMIT à close × (1 + 10 bps), actif à partir de la bougie suivant la décision, expire après 2 bougies. |
| Stop | close − 1,5 × ATR14 (règle ATR, figée en v1). |
| Invalidation avant remplissage | Aucune autre que l'expiration : l'ordre reste au repos jusqu'à ENTRY_EXPIRES_AT. Un remplissage à un prix ≤ stop (ouverture en gap) est suivi d'une vente au marché immédiate (SL_GAP), jamais d'un fill « au stop ». |
| Expirations (TXT V3) | ENTRY_EXPIRES_AT = décision + 2 bougies 15m (fenêtre d'entrée, identique au backtest) ; EXPIRES_AT = création + 15 min (acceptation du message par le consommateur), plafonné à ENTRY_EXPIRES_AT. |
| Nouveau setup | Un seul setup actif par paire et par stratégie. Tout candidat apparu pendant qu'un ordre est au repos ou qu'une position est ouverte est compté DUPLICATE et abandonné (jamais mis en file). Un nouveau setup est possible dès la clôture de la bougie où l'ordre a expiré ou la position s'est fermée ; en direct, le registre refuse aussi un second signal non expiré pour la même paire et stratégie. |
| Sortie | Un TP de référence à 2R (politique FIXED_SL_ONE_TP_V1) ; sortie au close après 96 bougies (24 h) — profil théorique, BSM n'a pas de sortie temporelle. |
| Coûts | Scénarios central / défavorable / stress (config/default.toml). Veto si RR net TP1 < 1,2 en coûts centraux. |
| Modes d'échec | Fausses cassures (retour sous le niveau), entrée tardive, frais disproportionnés sur petites amplitudes, cassures en fin de tendance 1h. |
| Plage de paramètres (lot 2) | Grille de recalibrage retenue le 2026-09-30, **après** avoir vu le backtest de référence du lot 1 : lookback {20, 40} × stop_atr {1,0 ; 1,5 ; 2,0} × target_r {1,5 ; 2,0 ; 3,0} = 18 essais. volume_multiple reste à 1,5 : l'apport du filtre volume est mesuré par ablation. |
| Comparaisons | Même cassure sans filtre volume ; sans filtre de contexte ; buy-and-hold ; cash. |
| Validation | docs/PROTOCOL.md : walk-forward purgé (lot 2), test final réservé depuis 2025-07-01. |
| Critère d'abandon | E[R] net ≤ 0 en coûts centraux sur l'ensemble hors échantillon, OU IC95 bootstrap par blocs entièrement ≤ 0,05 R, OU résultat porté par une seule année/paire, OU disparition sous le scénario défavorable. |
