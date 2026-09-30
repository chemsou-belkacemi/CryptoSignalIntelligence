# Fiche d'hypothèse — C. RANGE_REENTRY (v1)

Statut : **RESEARCH**. Rédigée le 2026-09-30, avant tout backtest de cette stratégie. Paramètres = points de départ expérimentaux, ni optimaux ni réputés rentables.

| Rubrique | Contenu |
|---|---|
| Hypothèse | Dans un range stable, une excursion sous la bande de Bollinger basse suivie d'une réintégration revient vers le centre du range plus souvent que ne l'exigent le risque et les coûts. |
| Mécanisme supposé | Liquidité de range, prises de bénéfices des vendeurs, absence de catalyseur directionnel. Non vérifiable avec l'OHLCV seul. |
| Régime attendu | Tendance 1h RANGE (ni BULL ni BEAR), sans stress BTC (filtres testés par ablation). |
| Univers | BTCUSDT, ETHUSDT (lot 2) ; 16 paires de docs/UNIVERSE.md depuis le 2026-09-30. Aucune généralisation revendiquée. |
| Données | Chandeliers 15m (setup) et 1h (contexte), BTC 1h pour le stress, bougies clôturées uniquement. |
| Règle exacte | Bollinger(20) avec écart-type de population ; basse = centre − 2 écarts-types. **Excursion** : close(t−1) < basse(t−1) ; **réintégration** : close(t) > basse(t) ; largeur totale de bande ≥ 0,5 % du centre ; contexte 1h RANGE ; rendement 24 h de BTC ≥ −3 %. RSI14 ≤ 35 : extension testée séparément, inactive en v1. |
| Entrée | LIMIT à close × (1 + 10 bps), active à la bougie suivante, expire après 2 bougies. |
| Stop | plus bas des 3 dernières bougies (excursion comprise) − 0,5 ATR14. |
| Invalidation avant remplissage | Aucune autre que l'expiration : l'ordre reste au repos jusqu'à ENTRY_EXPIRES_AT. Un remplissage à un prix ≤ stop (ouverture en gap) est suivi d'une vente au marché immédiate (SL_GAP), jamais d'un fill « au stop ». |
| Expirations (TXT V3) | ENTRY_EXPIRES_AT = décision + 2 bougies 15m (fenêtre d'entrée, identique au backtest) ; EXPIRES_AT = création + 15 min (acceptation du message par le consommateur), plafonné à ENTRY_EXPIRES_AT. |
| Nouveau setup | Un seul setup actif par paire et par stratégie. Tout candidat apparu pendant qu'un ordre est au repos ou qu'une position est ouverte est compté DUPLICATE et abandonné (jamais mis en file). Un nouveau setup est possible dès la clôture de la bougie où l'ordre a expiré ou la position s'est fermée ; en direct, le registre refuse aussi un second signal non expiré pour la même paire et stratégie. |
| Sortie | Cible = centre de bande à la décision (prix arrondi vers le bas), un seul TP (FIXED_SL_ONE_TP_V1) ; sortie au close après 96 bougies (profil théorique). |
| Coûts | Scénarios central / défavorable / stress. Veto si RR net < 0,8 en coûts centraux : la cible est plus proche qu'en tendance, ce seuil est lui-même une hypothèse. |
| Modes d'échec | Début de tendance baissière (le range casse), range trop étroit pour couvrir les coûts. Pas d'accumulation d'entrées pour « se refaire » : un seul setup actif par paire. |
| Plage de paramètres | Grille de recalibrage : bb_k {2,0 ; 2,5} × stop_buffer_atr {0,25 ; 0,5 ; 1,0} = 6 essais. |
| Comparaisons | Sans filtre de contexte ; sans filtre BTC ; avec filtre RSI ; v1 sans recalibrage ; buy-and-hold ; cash. |
| Validation | Walk-forward purgé (docs/PROTOCOL.md) ; test final réservé depuis 2025-07-01. |
| Critère d'abandon | Verdict REJECTED, OU résultat porté par une seule année/paire, OU disparition en coûts défavorables. |
