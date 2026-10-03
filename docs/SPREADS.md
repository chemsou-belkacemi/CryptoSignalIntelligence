# Écarts de prix entre bourses et prime coréenne (point 9 du plan) — relevé en service depuis le 2026-10-03

Code : `forward/spreads.py`. Tests : `tests/test_spreads.py`. Journal : `<CSI_ROOT>/forward/F0_ECARTS.jsonl` (ajout
seul, comme les autres relevés). Commande : `csi spreads` (résumé).

**Question.** Un particulier peut-il gagner de l'argent en arbitrant les prix entre bourses, comme le promettent
certains bots ? On mesure l'écart, on ne trade rien.

**Relevé.** Toutes les 10 minutes, au passage de détection des tests en direct : meilleurs prix d'achat et de vente de
BTC, ETH, SOL et XRP sur Binance (carnet public), Coinbase, Kraken, Bitstamp et OKX ; prix d'Upbit en KRW rapportés à
KRW-USDT (prime coréenne). Prix en USD ramenés en USDT par le cours USDT/USD de Kraken. Tout passe par les clients en
liste blanche (`data/http.py`, `forward/sources.py`, où l'API de marché d'OKX et le carnet d'Upbit ont été ajoutés) ;
**`ccxt` n'est pas utilisé en service**, parce que ses appels réseau contournent la liste blanche. Il reste installé
pour la recherche hors ligne.

**Mesure.** Pour chaque bourse face à Binance et chaque actif : l'aller-retour le plus favorable (acheter au prix de
vente de l'une, vendre au prix d'achat de l'autre), brut puis **net des frais taker publics d'un particulier** (Binance
7,5 pb avec la remise BNB ; Coinbase 60, Kraken 40, Bitstamp 40, OKX 10). Ni frais de retrait, ni délai de transfert,
ni fonds immobilisés : un écart net positif reste un maximum théorique.

**Premier relevé (2026-10-03).** Écarts bruts de −1 à +1,3 pb sur toutes les bourses et tous les actifs, soit −16 à
−68 pb nets ; prime coréenne de +8 à +12 pb. Le résumé à plusieurs semaines dira la part des relevés à écart net
positif ; la réponse attendue, pour un particulier, est « presque jamais ».

**Limites.** Un relevé toutes les 10 minutes ne voit pas les écarts de quelques secondes (ceux que prennent les
robots colocalisés) ; ce sont précisément ceux qu'un particulier ne peut pas prendre.
