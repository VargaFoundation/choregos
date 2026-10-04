# SPDX-License-Identifier: Apache-2.0
# Le greffon de l'ontologie, déclaré au cœur comme le ferait `pip install` : un `.dist-info` qui porte
# ses deux points d'entrée. Son code est déjà dans l'image (membre de l'espace de travail uv).
ARG BASE
FROM ${BASE}
USER 0
RUN d=/app/.venv/lib/python3.12/site-packages/choregos_ontology_greffon-0.12.0.dist-info \
 && mkdir -p "$d" \
 && printf 'Metadata-Version: 2.1\nName: choregos-ontology-greffon\nVersion: 0.12.0\n' > "$d/METADATA" \
 && printf '[choregos.plugins]\nchoregos-ontology = choregos_ontology.service.plugin:brancher\n\n[choregos.migrations]\nchoregos-ontology = choregos_ontology.service.plugin:MIGRATIONS\n' > "$d/entry_points.txt"
USER 1000
