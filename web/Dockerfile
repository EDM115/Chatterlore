# syntax=docker/dockerfile:1

FROM node:24.19.0-bookworm-slim AS dependencies

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    NODE_OPTIONS=--max-old-space-size=640 \
    pnpm_config_network_concurrency=1 \
    pnpm_config_child_concurrency=1

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@11.24.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pzp-ai-web-pnpm,target=/pnpm/store pnpm install --frozen-lockfile --prod

FROM dependencies AS build

ENV NODE_OPTIONS=--max-old-space-size=1024

COPY . .
RUN pnpm build

FROM node:24.19.0-bookworm-slim AS runtime

ENV NITRO_HOST=0.0.0.0 \
    NITRO_PORT=3000 \
    NODE_ENV=production

WORKDIR /app

COPY --from=build --chown=node:node /app/.output ./.output

USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then((response) => { if (!response.ok) process.exit(1) }).catch(() => process.exit(1))"]

CMD ["node", ".output/server/index.mjs"]
