"""Lot 5 bis : ML intraday (protocole docs/ML_INTRADAY.md, déclaré avant exécution).

Couches séparées : `dataset` (variables causales et cibles), `models` (logistique, LightGBM,
XGBoost), `portfolio` (règles d'entrée/sortie, taille, limites, exécution simulée, références),
`protocol` (sélection glissante sur DEVELOPMENT, estimation unique sur FINAL_TEST). Aucun ordre.
"""
