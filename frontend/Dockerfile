FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

ARG VITE_APP_TOKEN=""
RUN VITE_APP_TOKEN="$VITE_APP_TOKEN" npm run build

FROM node:22-alpine

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=5173 \
    BACKEND_HOST=backend \
    BACKEND_PORT=8000

WORKDIR /app

COPY --from=build /app/dist ./dist
COPY server.mjs ./

USER node

EXPOSE 5173

CMD ["node", "server.mjs"]
