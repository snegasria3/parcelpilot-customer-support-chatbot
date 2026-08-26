FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . .

# Build the SQLite snapshot and learned BGE/Chroma index into the image.
# No Groq key or application secret is used during the build.
RUN SESSION_SECRET=build-only-secret-not-used-at-runtime-000000000000000000 \
    GROQ_API_KEY= \
    python -m backend.cli bootstrap --rebuild-index

RUN useradd --create-home --uid 10001 parcelpilot \
    && chown -R parcelpilot:parcelpilot /app
USER parcelpilot

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
