# Frontend — React + Vite dev server
#
# Development-oriented image: source is bind-mounted by docker-compose, so
# Vite's HMR applies edits without a rebuild. Rebuild only when package.json
# changes. For production you would instead `npm run build` and serve the
# static output from nginx.
FROM node:20-alpine

WORKDIR /app

# Copied first so the dependency layer is cached across source edits.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
