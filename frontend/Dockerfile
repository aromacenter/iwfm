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
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
