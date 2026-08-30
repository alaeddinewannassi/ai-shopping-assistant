# Extends the stock PrestaShop image with the real chatbot widget baked into every
# storefront page's footer — so `docker compose up` gives you a live store with the widget
# already embedded, no manual admin-panel module install.
#
# WIDGET_TENANT_KEY must be a FIXED value (not randomly generated) — it has to match the
# widget key the provisioning step later issues in the tenancy DB for this same store,
# and both this build and that step run independently. See docker/prestashop/README.md.
#
# Named volumes (`prestashop_data`/`prestashop_data_two` in docker-compose.yml) are only
# seeded from the image on first creation — if you already ran `docker compose up` with the
# stock image before this Dockerfile existed, run `docker compose down -v` once to drop the
# old volumes so they reseed from this image.

FROM node:20-alpine AS widget-build
WORKDIR /widget
COPY chatbot/widget/package.json chatbot/widget/package-lock.json ./
RUN npm ci
COPY chatbot/widget/ ./
RUN npm run build

FROM prestashop/prestashop:8-apache
ARG WIDGET_TENANT_KEY
ARG WIDGET_API_BASE=http://localhost:8000
RUN test -n "$WIDGET_TENANT_KEY" || (echo "WIDGET_TENANT_KEY build arg is required" && exit 1)

COPY --from=widget-build /widget/dist/assistant-widget.js /var/www/html/assistant-widget.js
RUN printf '\n<script src="/assistant-widget.js"></script>\n<assistant-chat-widget api-base="%s" tenant-key="%s"></assistant-chat-widget>\n' \
      "$WIDGET_API_BASE" "$WIDGET_TENANT_KEY" \
      >> /var/www/html/themes/classic/templates/_partials/footer.tpl
