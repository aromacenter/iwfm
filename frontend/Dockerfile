# Iwfm frontend — Railway / bármely Docker host
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# A NEXT_PUBLIC_* értékek build-időben égnek be. Railway a service
# variable-öket build arg-ként átadja, ha ARG-ként deklaráljuk.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# A Flotta által telepített példányok API_PROXY_TARGET-et kapnak — a Next a
# /api átirányítás célját BUILD-kor rögzíti, ezért ennek is ARG-nak kell
# lennie, különben localhost:8000-re mutat és a kész példányon /api → 404
# (Aromabox-tanulság, 2026-10-27).
ARG API_PROXY_TARGET
ENV API_PROXY_TARGET=$API_PROXY_TARGET
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# A next.config.ts rewrites futásidőben is kell (API proxy célja) — a
# NEXT_PUBLIC_API_URL-t a runtime stage-ben is be kell állítani.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG API_PROXY_TARGET
ENV API_PROXY_TARGET=$API_PROXY_TARGET
COPY --from=build /app/package.json /app/package-lock.json /app/next.config.ts ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
