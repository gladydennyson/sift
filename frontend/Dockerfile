FROM node:20-alpine AS dependencies

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder

WORKDIR /app/frontend

COPY --from=dependencies /app/frontend/node_modules ./node_modules
COPY frontend/ ./

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

RUN npm run build

FROM node:20-alpine AS runner

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

COPY --chown=node:node --from=builder /app/frontend/.next/standalone ./
COPY --chown=node:node --from=builder /app/frontend/.next/static ./.next/static

USER node

EXPOSE 3000

CMD ["node", "server.js"]
