# SPDX-License-Identifier: Apache-2.0
# Binaire statique (musl), copié dans une image sans Python : busybox (ADR 0015).
FROM rust:1-alpine AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
COPY Cargo.toml ./
COPY src ./src
RUN cargo build --release && ls -l target/release/choregos-runtime-essai

FROM busybox:1.37.0
COPY --from=build /src/target/release/choregos-runtime-essai /runtime
USER 10001:10001
ENTRYPOINT ["/runtime"]
