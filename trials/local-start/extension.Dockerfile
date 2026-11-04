# SPDX-License-Identifier: Apache-2.0
# The ontology extension, declared to the core as `pip install` would: a `.dist-info` that carries its
# two entry points. Its code is already in the image (a member of the uv workspace).
ARG BASE
FROM ${BASE}
USER 0
RUN d=/app/.venv/lib/python3.12/site-packages/choregos_ontology_extension-0.12.0.dist-info \
 && mkdir -p "$d" \
 && printf 'Metadata-Version: 2.1\nName: choregos-ontology-extension\nVersion: 0.12.0\n' > "$d/METADATA" \
 && printf '[choregos.plugins]\nchoregos-ontology = choregos_ontology.service.plugin:register\n\n[choregos.migrations]\nchoregos-ontology = choregos_ontology.service.plugin:MIGRATIONS\n' > "$d/entry_points.txt"
USER 1000
