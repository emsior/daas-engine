# Kokpit DaaS Engine Cloud (Next.js 16, output: standalone).
# Budowanie z katalogu web/:  docker build -t daas-engine-web:2.0.0 web
#
# NEXT_PUBLIC_API_ORIGIN jest wstawiane do paczki w czasie budowania (build-time):
#   pusty (domyslnie) = ten sam origin co kokpit (Caddy: /api/* -> api, reszta -> web),
#   np. http://127.0.0.1:8000 = API na osobnym porcie (tryb bez reverse proxy).
# Zmiana wartosci wymaga ponownego zbudowania obrazu.

# ---- 1. zaleznosci (warstwa cache'owana, dopoki package*.json sie nie zmieni) ----
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- 2. budowanie ----
FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_ORIGIN=
ENV NEXT_PUBLIC_API_ORIGIN=${NEXT_PUBLIC_API_ORIGIN}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- 3. runtime: tylko paczka standalone, bez roota ----
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Pliki wlasciciela root, tylko do odczytu dla uzytkownika `node` (uid 1000): proces nie moze ich zmienic.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
# Jedyny zapisywalny katalog: cache Next.js (bez niego serwer loguje ostrzezenia o zapisie).
RUN mkdir -p .next/cache && chown node:node .next/cache

USER node
EXPOSE 3000

# /login jest trasa publiczna (nie pyta API o sesje), wiec kontrola dziala przy niedostepnym API.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:3000/login || exit 1

CMD ["node", "server.js"]
