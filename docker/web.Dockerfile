# Front Next.js : build autonome (`output: standalone` non requis, on garde .next/).
FROM node:24-bookworm-slim AS builder
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app
COPY apps/web/package.json apps/web/pnpm-lock.yaml* apps/web/pnpm-workspace.yaml ./apps/web/
COPY packages/contracts/ts ./packages/contracts/ts
WORKDIR /app/apps/web
RUN pnpm install --frozen-lockfile || pnpm install
COPY apps/web ./
# Read by `next build`, not by `next start`: the `/api/v1` rewrite and `NEXT_PUBLIC_*` are baked into
# the build. The defaults are what the code assumes when they are unset; the local install
# (`compose.yaml`) passes the API's address on its network and turns development sign-in on.
ARG CHOREGOS_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_DEV_LOGIN=false
ARG NEXT_PUBLIC_DEV_LOGIN_EMAIL=
ARG NEXT_PUBLIC_DEV_LOGIN_NEXT=
RUN pnpm build

# A production `node_modules`, rebuilt on its own.
#
# The development dependencies must not climb into the runtime image, and it is not only a
# question of size: Trivy found two CRITICALs in the published image, both coming from there —
# `esbuild` (via vitest) built with Go 1.20.12 (CVE-2024-24790) and `tar` 7.5.11 (via
# Playwright, CVE-2026-59873). Neither is run in production.
#
# `pnpm prune --prod` is not enough: it removes the top-level links and leaves the whole
# virtual store `.pnpm` — checked, `esbuild@0.21.5` and `@playwright+test` were still there, and
# `.pnpm` is what Trivy reads. A clean install into an empty tree is the only one that starts
# from the `dependencies` alone.
FROM node:24-bookworm-slim AS deps-prod
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/pnpm-lock.yaml* apps/web/pnpm-workspace.yaml ./
COPY packages/contracts/ts /app/packages/contracts/ts
RUN pnpm install --prod --frozen-lockfile --ignore-scripts

FROM node:24-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# No `useradd` here: the `node` image already provides a non-root `node` user with UID 1000,
# and creating a second account on the same UID makes `useradd` exit with 4
# ("UID already in use"). It is the UID that counts for Kubernetes, not the name.
#
# npm is removed: nothing calls it here (`next` is started directly) and it ships its own
# `tar` 7.5.11 — the second of the two CRITICALs Trivy found (CVE-2026-59873). An image that
# does not need a package manager must not carry one.
# Debian's security fixes, applied at build time: the base image (a tag, rebuilt at its own pace)
# can carry a package already fixed in `bookworm-security` — perl-base 5.36.0-7+deb12u3 and its
# three CRITICALs stopped release 0.16.0 at the Trivy scan.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx
WORKDIR /app
# What `next start` needs, and nothing else: the build, the manifest, and the config —
# which carries the CSP headers and the `/api/v1` relay, so it is not optional.
COPY --from=builder --chown=1000:1000 /app/apps/web/.next ./.next
COPY --from=builder --chown=1000:1000 /app/apps/web/package.json ./package.json
COPY --from=builder --chown=1000:1000 /app/apps/web/next.config.mjs ./next.config.mjs
COPY --from=deps-prod --chown=1000:1000 /app/apps/web/node_modules ./node_modules
COPY --from=builder --chown=1000:1000 /app/packages/contracts/ts ../../packages/contracts/ts
USER 1000
EXPOSE 3000
# `next` directly, not `pnpm start`: a package manager at the start of a production
# container only adds an indirection and more dependencies.
CMD ["node_modules/.bin/next", "start", "--port", "3000"]
