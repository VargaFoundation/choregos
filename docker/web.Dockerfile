# Front Next.js : build autonome (`output: standalone` non requis, on garde .next/).
FROM node:22-bookworm-slim AS builder
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./apps/web/
COPY packages/contracts/ts ./packages/contracts/ts
WORKDIR /app/apps/web
RUN pnpm install --frozen-lockfile || pnpm install
COPY apps/web ./
RUN pnpm build

FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos
WORKDIR /app
COPY --from=builder --chown=1000:1000 /app/apps/web ./
COPY --from=builder --chown=1000:1000 /app/packages/contracts/ts ../../packages/contracts/ts
USER 1000
EXPOSE 3000
CMD ["pnpm", "start"]
