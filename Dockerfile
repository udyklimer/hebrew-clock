# ==========================================
# Stage 1: Build Stage
# ==========================================
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# התקנת התלויות הנדרשות לקומפילציה בלבד
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    libfreetype6-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libraqm-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# בניית Pillow והתקנת החבילות
RUN pip install --no-cache-dir --no-binary=Pillow -r requirements.txt \
    && python -c "from PIL import features; assert features.check('raqm'), 'raqm missing!'"

# ==========================================
# Stage 2: Runtime Stage
# ==========================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# התקנת ספריות הרצה בלבד (Runtime Libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libjpeg62-turbo \
    libfreetype6 \
    libharfbuzz0b \
    libfribidi0 \
    libraqm0 \
    && rm -rf /var/lib/apt/lists/*

# העתקת התלויות שהותקנו בשלב ה-builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY app/ /app/app/
COPY *.ttf sleeping.png* /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
