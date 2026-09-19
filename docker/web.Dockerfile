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

# Un `node_modules` de production, reconstruit à part.
#
# Les dépendances de développement ne doivent pas monter dans l'image d'exécution, et ce n'est
# pas qu'une question de taille : Trivy a trouvé deux CRITICAL dans l'image publiée, tous deux
# venus de là — `esbuild` (via vitest) compilé avec Go 1.20.12 (CVE-2024-24790) et `tar` 7.5.11
# (via Playwright, CVE-2026-59873). Ni l'un ni l'autre n'est exécuté en production.
#
# `pnpm prune --prod` ne suffit pas : il retire les liens de premier niveau et laisse le magasin
# virtuel `.pnpm` entier — vérifié, `esbuild@0.21.5` et `@playwright+test` y étaient encore, et
# c'est `.pnpm` que Trivy lit. Une installation propre dans un arbre vide est la seule qui parte
# des seules `dependencies`.
FROM node:22-bookworm-slim AS deps-prod
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./
COPY packages/contracts/ts /app/packages/contracts/ts
RUN pnpm install --prod --frozen-lockfile --ignore-scripts

FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# Pas de `useradd` ici : l'image `node` fournit déjà un utilisateur non-root `node` en
# UID 1000, et créer un second compte sur le même UID fait sortir `useradd` en 4
# (« UID already in use »). C'est l'UID qui compte pour Kubernetes, pas le nom.
#
# npm est retiré : rien ne l'appelle ici (on démarre `next` directement) et il embarque son
# propre `tar` 7.5.11 — le second des deux CRITICAL trouvés par Trivy (CVE-2026-59873). Une
# image qui n'a pas besoin d'un gestionnaire de paquets ne doit pas en porter un.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx
WORKDIR /app
# Ce dont `next start` a besoin, et rien d'autre : le build, le manifeste, et la config —
# qui porte les en-têtes CSP et le relais `/api/v1`, donc elle n'est pas optionnelle.
COPY --from=builder --chown=1000:1000 /app/apps/web/.next ./.next
COPY --from=builder --chown=1000:1000 /app/apps/web/package.json ./package.json
COPY --from=builder --chown=1000:1000 /app/apps/web/next.config.mjs ./next.config.mjs
COPY --from=deps-prod --chown=1000:1000 /app/apps/web/node_modules ./node_modules
COPY --from=builder --chown=1000:1000 /app/packages/contracts/ts ../../packages/contracts/ts
USER 1000
EXPOSE 3000
# `next` directement, pas `pnpm start` : un gestionnaire de paquets au démarrage d'un
# conteneur de production n'apporte qu'une indirection et des dépendances en plus.
CMD ["node_modules/.bin/next", "start", "--port", "3000"]
