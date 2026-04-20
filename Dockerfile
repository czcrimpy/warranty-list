# --- Tailwind CSS ---
FROM node:22-alpine AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tailwind.config.js ./
COPY static/src/tailwind-input.css ./static/src/
COPY templates/ ./templates/
RUN mkdir -p static/css && npx tailwindcss -i static/src/tailwind-input.css -o static/css/tailwind.css --minify

# --- Flask ---
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8087 \
    FLASK_DEBUG=false

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY i18n.py .
COPY warranty_pdf.py .
COPY locales/ ./locales/
COPY templates/ ./templates/
COPY static/ ./static/
COPY --from=css /build/static/css/tailwind.css ./static/css/tailwind.css

EXPOSE 8087

CMD ["python", "app.py"]
