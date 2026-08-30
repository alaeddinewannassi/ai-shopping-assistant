FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# VITE_API_BASE is baked in at build time (Vite inlines import.meta.env.* at build) — set it
# as a build arg so a deployment can point at its own backoffice/backend URL.
ARG VITE_API_BASE=http://localhost:8001
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
