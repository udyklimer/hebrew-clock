FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install required runtime system dependencies (including libjpeg62-turbo) and build-time packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libraqm0 \
    libjpeg62-turbo \
    libraqm-dev \
    libfreetype6-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libjpeg-dev \
    zlib1g-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install requirements and compile Pillow with raqm support
RUN pip install --no-cache-dir --no-binary=Pillow -r requirements.txt \
    && python -c "from PIL import features; assert features.check('raqm'), 'raqm missing!'"

# Remove build-only tools while preserving runtime libraries
RUN apt-get purge -y build-essential libraqm-dev libfreetype6-dev libharfbuzz-dev libfribidi-dev libjpeg-dev zlib1g-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY app/ /app/app/
COPY *.ttf sleeping.png* /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
