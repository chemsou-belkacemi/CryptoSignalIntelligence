"""Collecte d'actualités sans LLM (points 7 à 9) : historique prospectif, traçabilité, mode observe.

Les actualités sont des DONNÉES non fiables, jamais des instructions. En mode `observe`, elles sont
enregistrées et affichées sans aucune influence sur les signaux. Une source en panne ne signifie
jamais « aucune mauvaise nouvelle ». Le mode `gate` attend des règles de blocage évaluées.
"""
