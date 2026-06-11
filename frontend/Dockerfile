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
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# A next.config.ts rewrites futásidőben is kell (API proxy célja) — a
# NEXT_PUBLIC_API_URL-t a runtime stage-ben is be kell állítani.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=build /app/package.json /app/package-lock.json /app/next.config.ts ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
