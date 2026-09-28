FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1. Install both runtime and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libjpeg62-turbo \
    libfreetype6 \
    libharfbuzz0b \
    libfribidi0 \
    libraqm0 \
    libjpeg-dev \
    libfreetype6-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libraqm-dev \
    zlib1g-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 2. Install Python packages & build Pillow from source with Raqm support
RUN pip install --no-cache-dir --no-binary=Pillow -r requirements.txt \
    && python -c "from PIL import features; assert features.check('raqm'), 'raqm missing!'"

# 3. Purge build tools & explicitly mark runtime packages as manually installed so autoremove won't delete them
RUN apt-get markmanual libjpeg62-turbo libfreetype6 libharfbuzz0b libfribidi0 libraqm0 \
    && apt-get purge -y build-essential libjpeg-dev libfreetype6-dev libharfbuzz-dev libfribidi-dev libraqm-dev zlib1g-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY app/ /app/app/
COPY *.ttf sleeping.png* /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
