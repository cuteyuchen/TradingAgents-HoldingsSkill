FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY docker-server.mjs ./docker-server.mjs
ENV PORT=80
EXPOSE 80
CMD ["node", "docker-server.mjs"]
