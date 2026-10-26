# Image de l'agent runner. À construire depuis la racine du dépôt :
#   docker build -f runner/Dockerfile -t fluxonata-runner .
# L'agent lance chaque action dans un conteneur frère (socket Docker de l'hôte). Voir docs/RUNNERS.md pour les
# précautions : le socket Docker donne un contrôle équivalent à root sur l'hôte, d'où l'intérêt d'un hôte dédié au runner.
FROM docker:27-cli AS dockercli

FROM python:3.13-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN useradd --system --uid 10001 --create-home runner
WORKDIR /opt/fluxonata
COPY runner/agent.py runner/agent.py
COPY backend/runs/__init__.py backend/runs/__init__.py
COPY backend/runs/runners.py backend/runs/runtime_config.py backend/runs/
COPY workers workers
ENV ORCH_BACKEND_DIR=/opt/fluxonata/backend ORCH_WORKERS_DIR=/opt/fluxonata/workers PYTHONUNBUFFERED=1
USER 10001
ENTRYPOINT ["python", "/opt/fluxonata/runner/agent.py"]
