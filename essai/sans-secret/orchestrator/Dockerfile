# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim
RUN pip install --no-cache-dir -q "kubernetes==31.*"
COPY orchestrate.py /app/orchestrate.py
USER 10001:10001
ENTRYPOINT ["python", "/app/orchestrate.py"]
