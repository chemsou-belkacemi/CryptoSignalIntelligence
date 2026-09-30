# syntax=docker/dockerfile:1
# CryptoSignalIntelligence : surveillance shadow (`run`) ; aucune clé, aucun ordre.
# Code en lecture seule dans /app ; tout l'état vit dans le volume /srv/csi (CSI_ROOT).

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CSI_ROOT=/srv/csi \
    CSI_CONFIG_FILE=/app/config/default.toml \
    ARROW_DEFAULT_MEMORY_POOL=system \
    MALLOC_ARENA_MAX=2
# Mémoire : l'allocateur système (au lieu du pool d'Arrow) et 2 arènes malloc rendent la mémoire au
# système entre deux cycles ; sans cela le processus gardait ~600 Mo au repos.

WORKDIR /app

RUN groupadd --system --gid 10002 csi \
    && useradd --system --uid 10002 --gid csi --create-home --home-dir /home/csi csi

# pylock.toml a été verrouillé sous Windows (roues win_amd64 seulement) : sous Linux, on installe
# les MÊMES versions, extraites en contraintes (les empreintes des roues Linux ne sont pas vérifiées).
COPY pylock.toml pyproject.toml README.md ./
RUN python -c "import tomllib; d = tomllib.load(open('pylock.toml', 'rb')); \
open('constraints.txt', 'w').write(''.join(f\"{p['name']}=={p['version']}\n\" for p in d['packages']))"

COPY config ./config
COPY src ./src
RUN pip install -c constraints.txt . \
    && mkdir -p /srv/csi \
    && chown -R csi:csi /srv/csi

USER csi
VOLUME ["/srv/csi"]

# « Prêt » = un cycle récent terminé (pas seulement un processus vivant).
HEALTHCHECK --interval=60s --timeout=20s --start-period=25m --retries=3 \
    CMD ["python", "-m", "crypto_signal_intelligence", "health"]

CMD ["python", "-m", "crypto_signal_intelligence", "run", "--mode", "shadow"]
