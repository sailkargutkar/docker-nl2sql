# nl2sql — self-sustaining natural-language queries over Postgres

A lightweight, read-only NL → SQL layer with **no LLM call** in the hot path.
A local pipeline (rule-based intent fallback + TF-IDF classifier + schema-aware
matcher + template builder) generates SQL; sqlglot validates it; Postgres
enforces read-only at the session level; a single-page UI displays results.

Inspired by Dr. Shobha's talk "Natural Language to SQL Query conversion using
Machine Learning techniques" — we keep the rule-based skeleton, use WordNet
for synonym/hypernym enrichment, and replace the LSTM with a small TF-IDF +
LinearSVC intent classifier that retrains from this service's own query
history.

## Why this exists

- **No LLM keys, no API cost, no network dependency.** Everything runs in-container.
- **Deterministic**: the same question produces the same SQL.
- **Fast**: 10–50 ms end-to-end on CPU, vs. 1–3 s for an LLM.
- **Small**: Docker image ≈ 300 MB total (no PyTorch, no Elasticsearch, no Java).
- **Self-sustaining**: retraining consumes successful queries from
  `/data/history.db` — the service gets better the more it's used.

## What's in the box

- **FastAPI backend** (`app/main.py`) — `/api/ask`, `/api/schema`,
  `/api/history`, `/api/train`, plus `/` serving the UI.
- **Local generator** (`app/generator.py`) — pipeline that replaces the LLM:
  preprocess → intent → match → value extract → build.
- **NLP primitives** (`app/nlp/`) — tokenization, lemmatization (NLTK WordNet),
  rapidfuzz + synonym schema matching, value extraction, TF-IDF intent classifier.
- **SQL builder** (`app/builder.py`) — emits sqlglot AST, never string-concats.
- **Schema DSL** (`schema/*.yml`) — YAML whitelist for tables/columns and
  matcher context (the matcher uses declared `synonyms:` lists verbatim).
- **Bootstrap** (`scripts/bootstrap_schema.py`) — introspects a live Postgres
  DB into DSL YAML.
- **Trainer** (`scripts/fit.py` / `POST /api/train`) — rebuilds the intent
  classifier from SEED + `history.db`.
- **Validator** (`app/validator.py`) — sqlglot-based:
  - exactly one top-level SELECT;
  - rejects INSERT/UPDATE/DELETE/MERGE/DDL/DCL/TCL;
  - DSL table whitelist;
  - blocks `pg_read_file`, `dblink`, `pg_sleep`, etc.;
  - caps `LIMIT` at `MAX_ROWS`.
- **Executor** (`app/executor.py`) — sets `default_transaction_read_only=on`
  and `statement_timeout` on every connection.
- **History** — SQLite at `/data/history.db`, surfaced in the UI.
- **UI** (`static/index.html`) — single HTML file, no build step.

## Safety model (defense in depth)

```
NL question ──► local generator (deterministic; no external call)
            ──► sqlglot parse + AST check (single SELECT, whitelist, no dangerous fns)
            ──► LIMIT injection / cap
            ──► Postgres session: SET default_transaction_read_only = on
            ──► statement_timeout kills runaway queries
```

Even if every prior layer were bypassed, the read-only DB user blocks writes.

## Configuration

Copy `.env.example` to `.env` and fill in values. The important ones:

| Var | Purpose |
|---|---|
| `DB_*` | Connection to the tmt Postgres (use a **read-only user**) |
| `MAX_ROWS` | Hard cap on rows returned (default 500) |
| `STATEMENT_TIMEOUT_MS` | Postgres `statement_timeout` per query (default 15s) |
| `SCHEMA_FILE` | Path to the DSL YAML |
| `INTENT_MODEL_PATH` | Where the classifier is persisted (default `/data/intent.joblib`) |

### Create a read-only Postgres user (recommended)

```sql
CREATE ROLE nl2sql_reader LOGIN PASSWORD 'strong-password';
GRANT CONNECT ON DATABASE tmt TO nl2sql_reader;
GRANT USAGE ON SCHEMA public TO nl2sql_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO nl2sql_reader;
```

## Running

### Docker compose

```bash
docker compose up -d --build
```

Then open <http://localhost:8010>. On first boot the classifier is auto-trained
on the seed corpus (~1 second). Hit **Retrain** in the UI (or `POST /api/train`)
any time to incorporate new history rows.

### Dev (no container)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m nltk.downloader wordnet omw-1.4 stopwords
uvicorn app.main:app --reload --port 8010
```

## Extending coverage

**Tell the matcher about synonyms.** In the DSL, add a `synonyms:` list to any
column that users refer to by a different word:

```yaml
- name: materialStatus
  type: string
  synonyms: [marital_status, marriage, married]
```

**Tell the intent classifier about new phrasings.** Edit
`app/training/seed.py`, then `POST /api/train`. Or just run real queries — the
fitter picks up successful queries from `history.db` automatically and infers
their intent from the SQL shape (`SELECT COUNT(*)` → count, `ORDER BY … DESC
LIMIT` → top, and so on).

**Anything truly ambiguous** → the generator returns `sql: ""` with an
explanation; the UI asks the user to rephrase. Better than hallucinating.

## Limitations

- Coverage is bounded by the schema DSL + training corpus. An LLM still wins
  for novel phrasings, multi-hop logic, or schemas that haven't been annotated.
- Multi-table joins only work if the FK path is declared in the DSL (the
  bootstrap script does this automatically).
- No free-text search (yet). Add Postgres FTS (`tsvector`/`tsquery`) to the
  schema DSL for columns where descriptive matching matters.
