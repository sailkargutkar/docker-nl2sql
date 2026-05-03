# tools/ — LLM-as-build-oracle scripts

Offline scripts that use OpenAI to enrich the schema DSL, generate
training data, and label uncertain history rows.

**The runtime stays LLM-free.** The deployable Docker image installs
`requirements.txt` only; `openai` and `ruamel.yaml` from this directory's
`requirements.txt` are dev-only.

---

## Setup (one-time)

1. **Get an OpenAI API key** at https://platform.openai.com/api-keys
2. **Place it locally** (gitignored, never committed):
   ```bash
   echo 'OPENAI_API_KEY=sk-proj-...' > .env
   # OR
   echo 'OPENAI_API_KEY=sk-proj-...' > .claude/.env
   ```
   Either path works; the tools check both.
3. **Install dev dependencies** in your venv:
   ```bash
   .venv/bin/pip install -r tools/requirements.txt
   ```
4. **Verify**:
   ```bash
   python -c "from tools._common import get_api_key; print('ok' if get_api_key() else 'missing')"
   ```

---

## Available tools

### `annotate_schema.py` — enrich the DSL

Reads the schema YAML, asks the LLM for a one-line description and up to
five synonyms per column, writes a `<schema>.proposed.yml` for review.

```bash
# Trial: 5 tables, see what comes back.
python tools/annotate_schema.py --tables Client,Organization,Employee,Tour,Invoice

# Estimate cost without calling the API.
python tools/annotate_schema.py --tables all --dry-run

# Full schema (gpt-4o-mini, batched, ~$0.07).
python tools/annotate_schema.py --tables all

# Sensitive schema — send sanitized identifiers instead of real names.
python tools/annotate_schema.py --redact

# Skip DB sampling (faster, less context but no live values leaked).
python tools/annotate_schema.py --no-samples
```

Output goes to `schema/<name>.proposed.yml`. Apply when ready:

```bash
python tools/apply_annotations.py --review                # show diff
python tools/apply_annotations.py --apply                 # backup + merge
python tools/apply_annotations.py --discard               # throw it out
```

The `--apply` step backs up the current schema to
`schema/<name>.yml.bak.<timestamp>` before replacing it.

---

## Running in the background

The annotate/seed/benchmark tools are batch jobs — survive SSH
disconnect with `nohup`:

```bash
nohup python tools/annotate_schema.py --tables all \
      > tools/.cache/annotate.log 2>&1 &
```

The active-labeling daemon (when added) will accept `--watch INTERVAL`
to poll `history.db` continuously.

---

## Cost (gpt-4o-mini, default model)

Pricing as of 2026-05: $0.15/M input, $0.60/M output.

| Action | Approx tokens | Cost |
|---|---|---|
| 5-table trial annotation | 7K in / 5K out | ~$0.005 |
| Full 93-table schema | 120K in / 80K out | ~$0.07 |
| Generate 500 seed examples | 3K in / 25K out | ~$0.02 |
| Generate 50 benchmark pairs | 5K in / 8K out | ~$0.005 |
| Active label per uncertain query | 1K in / 200 out | ~$0.0003 |

Set a budget cap with `--max-cost USD` (default $1.00). Each script
estimates *before* calling the API and refuses to proceed if it would
exceed the cap.

---

## Cache

Every (model, prompt-template-version, prompt) tuple is cached on disk
under `tools/.cache/`. Re-running an unchanged annotation is **free** —
no API call. Cache invalidates automatically when:

- The prompt template version (`tools/_prompts.py`) is bumped
- The model is changed via `--model`
- The schema input changes (different prompt content)

The cache directory is gitignored.

---

## Security

| Guarantee | How |
|---|---|
| API key is never committed | `.env` and `.claude/.env` are gitignored; cache files contain no key |
| API key is never logged | Tools never print the key; loaded once via `python-dotenv` |
| Schema metadata is opt-in | `--redact` sends `T1, C1` instead of real names |
| DB content sample is bounded | 5 values × 30 chars max per column, configurable; off via `--no-samples` |
| Cost is bounded | `--max-cost` refuses runs over budget |
| Concurrent runs blocked | PID lockfile under `tools/.cache/locks/` |

If you accidentally leak a key:

1. Revoke immediately at https://platform.openai.com/api-keys
2. GitHub's secret-scanning may auto-revoke too — check email + dashboard
3. Generate a new key and place in `.env` only

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY not set` | Place key in `.env` or `.claude/.env` (see Setup) |
| `ModuleNotFoundError: openai` | `pip install -r tools/requirements.txt` |
| `another annotate_schema run is in progress` | Check `tools/.cache/locks/`; if no real process, delete the lock file |
| Cost estimate looks wrong | Pricing in `_common.py` may be stale — update `PRICING` |
| LLM returned bad JSON repeatedly | Bump `TEMPLATE_VERSION` in `_prompts.py` to invalidate cache, retry |
