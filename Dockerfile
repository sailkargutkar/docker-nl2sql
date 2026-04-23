FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NLTK_DATA=/opt/nltk_data

WORKDIR /app

# libpq is needed at runtime by psycopg2-binary's wheel on slim images,
# but gcc/libpq-dev build-deps are NOT: psycopg2-binary ships prebuilt.
# Keep the layer minimal.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake only the NLTK corpora we actually need. wordnet + omw-1.4 give us
# synonyms/hypernyms for enrichment; stopwords feeds the preprocessor.
RUN python -m nltk.downloader -d /opt/nltk_data wordnet omw-1.4 stopwords \
 && find /opt/nltk_data -name "*.zip" -delete

COPY . .

EXPOSE 8010

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
