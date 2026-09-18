# Workers Temporal. Même socle que l'API : un seul jeu de dépendances à auditer.
FROM ghcr.io/vargafoundation/choregos-api:latest AS base
USER 1000
WORKDIR /app
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "choregos_orchestrator.worker", "--queues", "orchestrator,executor,tracker,memory"]
