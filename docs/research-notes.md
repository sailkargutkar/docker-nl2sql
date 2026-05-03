# Text-to-SQL Research Notes — Papers A through G

Reference document capturing the literature review that informed the
`feature/self-sustaining-nl2sql` architecture. Each paper was analyzed for
transferable ideas given the project's hard constraints:

- **LLM-free** at inference time
- **Lightweight** Docker image (~300 MB)
- **Self-sustaining** — improves via `history.db` without external retraining

The recommendation has been stable since **Paper B** (paper 2 of 7); subsequent
papers added refinements (C, D) or corroborated existing ideas (E, F, G).

---

## Quick reference table

| Paper | Year | Source | Core idea | New contribution to our plan |
|---|---|---|---|---|
| **A** | 2023 | SQL-PaLM (Sun et al., arXiv 2306.00739) | LLM prompt engineering for text-to-SQL | DB content value matching (T1a) |
| **B** | 2017 | SQLizer (Yaghmazadeh et al., OOPSLA) | Sketch IR + type inhabitation + repair loop | **Core architecture** — sketch + repair + top-K |
| **C** | 2022 | Survey (Kumar et al., arXiv 2208.04415) | Survey of 24 neural text-to-SQL systems | MISP interactive disambiguation + execution-guided decoding |
| **D** | 2025 | SQL-R1 (Ma et al., NeurIPS) | RL-trained NL2SQL reasoning model (GRPO) | Multi-component scoring recipe |
| **E** | 2015 | Sathick & Jaya, IJST | Java/SQL Server NL→SQL system | Independently confirms MISP idea |
| **F** | 2018 | Bhalla & Gupta, Stanford CS | SQLNet reimplementation with column attention | None new (column attention ≈ our matcher) |
| **G** | 2023 | Jeong et al., MDPI Entropy | Hybrid sketch + generation decoder | SER metric (small instrumentation) |

---

## Conclusion — what we take from each paper

Out of seven papers, **four contributed concrete ideas** to the implementation;
**three were corroborative only**. Full attribution by paper:

### 🟢 Paper B — SQLizer → THE FOUNDATION

The single most influential paper. Provides the entire architectural backbone.

| Idea | Lands in |
|---|---|
| Explicit query-sketch IR with typed holes | `app/sketch.py` (new) |
| Quantitative type inhabitation — enumerate fillers, score each | `app/sketch.py` |
| Refinement / repair loop (fault localization → repair tactic → retry) | `app/repair.py` (new) |
| Repair tactics: add JOIN, swap aggregate ↔ column, add predicate, change table | `app/repair.py` |
| Top-K ranked candidates instead of single SQL | `app/generator.py` |
| Fault-aware error messages (which sketch position broke) | builder errors |

→ **Stage 1**, ~500 lines. Without B, we have no architecture.

### 🟢 Paper A — SQL-PaLM → VALUE PRECISION

| Idea | Lands in |
|---|---|
| **DB content matching (T1a)** — for each NL keyword, ILIKE + LCS against actual DB cell values | `app/nlp/values_db.py` (new) |
| Resolves `"shoes"` → `"Running shoes"`, `"california"` → `"California"`, `"acme"` → `"Acme Corp Pvt Ltd"` | Plugs into sketch's value-hole scoring |

→ **Stage 3**, ~150 lines. A.T1b (hand-curated hints) was **rejected** —
replaced by C's MISP loop which captures the same information organically.

### 🟢 Paper C — Survey → CLOSES THE LEARNING LOOP

| Idea | Source within survey | Lands in |
|---|---|---|
| **Interactive disambiguation (MISP)** — when top-1/top-2 confidence are close, ask the user to pick | MISP entry | UI button + `POST /api/teach` |
| **Execution-guided validation** — execute top-K read-only, discard errors, re-rank | PointerSQL, SeaD entries | `app/generator.py` post-repair |

→ **Stages 2 & 4**, ~140 lines. MISP is what makes the system **truly
self-sustaining** — every user click writes labeled data to `history.db`
for the next retrain.

### 🟢 Paper D — SQL-R1 → SCORING RECIPE

| Idea | Lands in |
|---|---|
| **Multi-component scoring** — Format + Execution + Result-plausibility + Length penalty | `app/generator.py` confidence function |
| Ablation: removing any component drops accuracy 0.7–2.7 % (additive, non-redundant signals) | Validates the 4-component approach |

→ Refines **Stage 2**, ~30 lines. Replaces a single confidence number with
the 4-signal sum.

### 🟡 Paper G — Jeong → METRIC ONLY

| Idea | Lands in |
|---|---|
| **Syntactic Error Rate (SER)** — log parse failures + validation failures + execution errors as separate counters | `/api/health` extension |

→ **Bonus**, ~15 lines. G's main idea (hybrid sketch + generation decoder)
corroborates Paper B but isn't a separately adopted item.

### ⚪ Papers E & F — corroboration only, nothing adopted

| Paper | What it confirmed | New code? |
|---|---|---|
| **E** Sathick 2015 | Independent confirmation of MISP idea (user picks from intermediate queries) | None — strengthens C's adoption |
| **F** Bhalla 2018 | Column attention ≈ our rapidfuzz + WordNet matcher | None — already covered |

---

### Final attribution at a glance

```
Stage 1: Sketch IR + repair loop + top-K            ← Paper B           ~500 lines
Stage 2: Execution-guided validation                ← Paper C
         + multi-component scoring                  ← Paper D            ~80 lines
Stage 3: DB content value matching                  ← Paper A.T1a       ~150 lines
Stage 4: Interactive top-K disambiguation UI        ← Paper C (MISP)
                                                       confirmed by E    ~60 lines
Bonus:   Syntactic Error Rate metric                ← Paper G            ~15 lines
                                                                       ─────────
                                                                  total: ~810 lines
```

### One-line summary per paper

- **B** gives us the **architecture** (sketch + repair).
- **A** gives us **value precision** (real DB values, not guesses).
- **C** gives us the **user feedback loop** (interactive disambiguation + execution validation).
- **D** gives us the **scoring formula** (4-signal confidence).
- **G** gives us **observability** (SER metric).
- **E, F** add nothing — but their independent agreement with B and C raises
  confidence we picked the right primitives.

### LLM ↔ non-LLM mapping

Each adopted idea is the **non-neural essence** of its LLM-era source:

| Idea pattern | LLM version | Our LLM-free version |
|---|---|---|
| Sketch fill | Neural slot decoder | Symbolic enumeration with rapidfuzz scoring |
| Repair | LLM retry with feedback | Deterministic tactic application |
| Value matching | Embedding similarity | ILIKE + longest common subsequence |
| Confidence | Softmax / RL reward | 4-component additive score |
| User loop | RLHF | UI click → history.db → retrain |
| Validation | Ground-truth comparison | Heuristic plausibility (rows > 0, sane size) |

The bet: for a fixed schema with curated synonyms (which the schema DSL
already provides), this stack hits the same accuracy ceiling LLMs do —
deterministic output, ~300 MB image, zero external API calls.

---

## Paper A — SQL-PaLM (arXiv 2306.00739)

**Citation**: Sun, R. et al. *SQL-PaLM: Improved Large Language Model Adaptation for Text-to-SQL (extended)*. 2023.

**Core idea**: Adapt PaLM-2 for text-to-SQL via few-shot prompting and instruction fine-tuning. Investigates input representation, training-data diversity, synthetic data, query-specific DB content, column selection, and test-time refinement.

**Stack**: PaLM-2 Unicorn LLM. Concise schema serialization. Execution-based consistency decoding. Hard vs soft column selection.

**What we adopted (T1a — DB content matching)**:
For each NL keyword, do a longest-common-subsequence match against actual
DB cell values using `ILIKE … LIMIT 5` per likely-relevant column. Resolves
`"shoes"` → `"Running shoes"` (real DB value), `"california"` → `"California"`.

**What we rejected**:
- Synthetic data via LLM (we can't generate)
- Fine-tuning / LoRA (LLM-only)
- Program-aided column selection (requires preliminary LLM SQL)
- Consistency decoding via sampling (we're deterministic)

**T1b (hand-curated hints) reshaped**: instead of static `H` block, capture
hints organically via interactive corrections (history-driven). The
sketch + repair loop absorbs most of what hints would cover.

---

## Paper B — SQLizer (OOPSLA 2017)

**Citation**: Yaghmazadeh, N., Wang, Y., Dillig, I., Dillig, T. *SQLizer: Query Synthesis from Natural Language*. PACMPL OOPSLA 2017.
*(Analyzed via the author's conference talk transcript)*

**Core idea**: Database-agnostic, **non-ML symbolic** approach. Pipeline:

1. **Semantic parsing** → query *sketch* (SELECT/FROM/WHERE skeleton with
   `?` holes, each hole carries an NL hint)
2. **Quantitative type inhabitation** → enumerate every valid filler from
   the schema; score each via hint-similarity + schema signals (PK/FK,
   DB content, column names)
3. **Refinement loop** → if no completion meets the confidence threshold:
   - **fault localization** identifies the faulty hole/clause
   - apply a **repair tactic** (add predicate, add JOIN, swap aggregate ↔
     column, change table)
   - retry until convergence or iteration cap
4. **Global top-K ranking** across all sketches + repairs

**Results**: ~90% top-5, ~81–85% top-1 across 3 databases. 1.2 s avg.
Database-agnostic, no DB-specific training.

**What we adopted (entire backbone)**:
- Explicit query-sketch IR with typed holes
- Confidence-scored completions
- Repair loop with fault localization + repair tactics
- Top-K output (not single SQL)
- Fault-aware error messages instead of "could not build"

**Why it dominates the plan**: B is the **only paper of the seven that
shares all our constraints** (LLM-free, deterministic, lightweight). It
also fixes the single biggest structural gap in our current code: today
we fail-fast on first generation error; B's loop tries variants until one
satisfies confidence + executability.

---

## Paper C — Survey (arXiv 2208.04415)

**Citation**: Kumar, A., Nagarkar, P., Nalhe, P., Vijayakumar, S. *Deep Learning Driven Natural Languages Text to SQL Query Conversion: A Survey*. 2022.

**Core idea**: Survey of 24 neural text-to-SQL systems (2018–2022) across
11 datasets (Spider, WikiSQL, ATIS, GeoQuery, …).

**Highlights catalogued**:
- SQLNet, TypeSQL, F-SQL, RYANSQL — sketch + slot-filling
- IRNet — SemQL intermediate representation
- BRIDGE — fuzzy NL ↔ DB cell value matching with anchor texts
- RAT-SQL — schema-as-graph, relation-aware self-attention
- PointerSQL, SeaD — execution-guided decoding (reject error-producing partials)
- SyntaxSQLNet — 9 specialized prediction modules
- **MISP** — interactive: ask user binary questions for low-confidence parts
- ValueNet — NER + heuristics for DB value candidates
- Seq2SQL, Seq2Tree — RL / grammar-constrained decoding

**What we adopted**:

1. **MISP-style interactive disambiguation**. When top-1 and top-2
   confidence are within a small margin, the UI shows both candidates and
   asks the user to pick. Each click writes to `history.db` and feeds the
   next classifier retrain. This is the **closing of the self-sustaining
   loop** — every ambiguity becomes labeled training data.

2. **Execution-guided validation** (PointerSQL / SeaD pattern). After the
   sketch+repair loop emits top-K, execute each candidate read-only,
   discard those that error, re-rank by row-count plausibility.

**Corroborates** (no new contribution but raises confidence in adopted ideas):
- Sketch-based synthesis is mainstream — SQLNet, TypeSQL, F-SQL, RYANSQL,
  *and* SQLizer all converge on it
- DB content matching is mainstream — BRIDGE, TypeSQL-content-sensitive,
  ValueNet, F-SQL, SQL-PaLM all use some form

**What we rejected**:
- Graph-based schema encoding (RAT-SQL) — heavy ML
- Pretraining (GRAPPA), RL with execution reward (Seq2SQL) — LLM-only
- BERT/Transformer encoders — out of scope

---

## Paper D — SQL-R1 (NeurIPS 2025)

**Citation**: Ma, P. et al. *SQL-R1: Training Natural Language to SQL Reasoning Model By Reinforcement Learning*. arXiv 2504.08600.

**Core idea**: Take a 7B/14B coder LLM (Qwen2.5-Coder), SFT cold-start on
synthetic SQL pairs, then RL with GRPO and a 4-component reward. Generate
N candidates at T=0.8, vote by executed result. Achieves 88.7% Spider /
67.1% BIRD on a 14B model.

**Cost**: 8 × 80 GB GPUs, weeks of training, 200 K – 2.5 M synthetic pairs.

**95% not applicable** to us (LLM, RL, training-heavy). But:

**What we adopted (multi-component scoring recipe)**:

The 4-component reward function in §2.3.3:
- **Format reward** — does output parse / have required structure?
- **Execution reward** — does the SQL run without error?
- **Result reward** — does the query result match (or look plausible)?
- **Length reward** — prefer compact SQL when tied on the rest

Their ablation (Table 5) shows removing any one component drops accuracy
0.7–2.7 % — they're additive, non-redundant signals.

For us, this becomes the **confidence function** for picking among
top-K candidates after Paper B's repair loop:

```
confidence(sql) = w_f * parses_cleanly(sql)
                + w_e * executes_without_error(sql)
                + w_r * result_plausibility(sql)   # rows > 0, non-null
                + w_l * simplicity_bonus(sql)
```

We replace the ground-truth check (which they have, we don't) with
heuristics: zero rows = probably wrong filter, all-null projection = bad
column choice, 10M rows returned = missing WHERE / LIMIT.

**What we rejected**:
- GRPO RL training, SFT, all training apparatus
- Reasoning-trace scaffolding (`<think>...</think>`) — only meaningful with an LLM
- Synthetic data at scale

---

## Paper E — Sathick & Jaya (IJST 2015)

**Citation**: Sathick, K. J., Jaya, A. *Natural language to SQL Generation for Semantic Knowledge Extraction in Social Web Sources*. Indian Journal of Science and Technology, Vol 8(1), Jan 2015.

**Honest assessment**: **Weakest of the seven.** Dated, narrow, methodologically thin.

**What it describes**: Java + SQL Server app for querying social-media data
(Facebook/Twitter/LinkedIn pulled via R-tool). 5-stage textbook NLP
pipeline (morphological → syntactic → semantic → discourse → pragmatic).
Hand-curated dictionary of synonyms. PK-FK auto-JOIN. String template
substitution into SQL.

**Evaluation**: precision/recall on a *single example query*. Precision
collapses to 0.0014 at recall 1.0 — the system retrieves a swamp of
irrelevant content to attain full recall.

**Architecturally subsumed**: every feature listed has a stronger
equivalent in our current code (NLTK WordNet > Porter stemming, sqlglot
AST > string templates, FK BFS > simple PK-FK, etc.).

**Only contribution**: the system asks the user to pick from "intermediate
queries". This is the **same idea as MISP** (Paper C) — independent
confirmation in 2015 of an idea formalized later. Strengthens the case
for adopting it but doesn't add anything mechanically new.

---

## Paper F — Bhalla & Gupta (Stanford CS, ~2018)

**Citation**: Bhalla, I., Gupta, A. *Generating SQL queries from natural language*. Stanford CS course project.

**Honest assessment**: Course project, narrow scope, second-weakest of seven.

**What it describes**: Reimplements SQLNet on WikiSQL with two tweaks:
1. Predict aggregate operator from question alone (not from predicted SELECT column)
2. Train SELECT and aggregate losses independently rather than mixed

Architecture: Bi-LSTM + GloVe + column attention + pointer network.
Improvement: 1.5–2.5 % over SQLNet baseline on WikiSQL **SELECT and
aggregate only**. No WHERE, no JOIN, no nested.

**Transferable to us**: nothing genuinely new.
- Column attention conceptually = our per-column rapidfuzz + WordNet score
- "Aggregate from question alone" already in our intent classifier
- Independent loss optimization → not applicable (we don't train an NN)

---

## Paper G — Jeong et al. (Entropy 2023)

**Citation**: Jeong, G. et al. *Improving Text-to-SQL with a Hybrid Decoding Method*. Entropy 25(3), 513, 2023.

**Core idea**: Combine sketch-based decoding (slot-filling) with
generation-based decoding (sequential token generation). Generate SQL
tokens sequentially, but each step's vocabulary is **type-masked** by
slot type. Pointer Network for column slots; Token Generation Layer for
non-column slots; **BIO sequence labeling** for WHERE values (one-shot
extraction, no copy mechanism).

Defines slot types: `sel_col`, `sel_agg`, `sel_cont`, `wh_col`, `wh_op`,
`wh_logic`, `wh_val`. Generation order ≠ written order; sort post-hoc.

**Strong corroboration** of Paper B: hybrid (sketch + sequential) is a
well-validated architecture. Paper G is independent confirmation that
type-masked sequential decoding beats either pure sketch or pure
generation.

**Maps onto our plan**:

| G's idea | Our equivalent |
|---|---|
| Type-masked decoding | Already in `builder.py` — switching on intent only emits valid AST shapes |
| BIO sequence labeling for values | `values.py` + `implicit.py` — span extraction in spirit |
| Hybrid sketch + generation | = Paper B's sketch + repair |
| Pointer Network for columns | rapidfuzz + WordNet scoring |
| Sorting generation order → written order | sqlglot AST handles this automatically |

**Only new contribution**: **Syntactic Error Rate (SER)** as an evaluation
metric. We can add it cheaply — count `validate_and_rewrite` failures and
expose at `/api/health`. ~15 lines.

---

## Where the seven papers converge

After analyzing all seven, the field has effectively converged on a small
set of architectural primitives:

1. **Sketch / IR with typed holes** (B, C-SQLNet/TypeSQL/F-SQL/RYANSQL, G)
2. **Schema linking via string + embedding match** (B, C-IRNet/RAT-SQL, F-attention)
3. **DB content / value matching** (A, C-BRIDGE/TypeSQL/ValueNet)
4. **Execution-guided validation / refinement** (B-repair, C-PointerSQL/SeaD, D-self-consistency)
5. **Multi-signal scoring** (B-confidence, D-4-component reward)
6. **Top-K + interactive disambiguation** (B, C-MISP, E)

Every one of these maps cleanly onto our LLM-free constraints when
reduced to its non-neural essence.

---

## Final implementation plan (Option 3+)

```
Stage 1 — Sketch IR + repair loop + top-K       (B, corroborated by C/G)
            ~500 lines + tests
            • app/sketch.py    — explicit dataclass IR with typed holes
            • app/repair.py    — repair tactics (add_join, swap_aggregate,
                                  add_predicate, change_table, swap_intent)
            • app/builder.py   — refactored to consume sketches
            • app/generator.py — drives the refinement loop (max 5 iter,
                                  ~200 ms cap)
            • UI               — show top-K candidates when confidence is close

Stage 2 — Execution-guided validation + multi-component scoring  (C + D)
            ~80 lines + tests
            • For each top-K candidate, execute read-only
            • Score: format + executes + result-plausibility + simplicity
            • Re-rank survivors; drop errors

Stage 3 — DB content value matching              (A.T1a)
            ~150 lines + tests
            • app/nlp/values_db.py — ILIKE + LCS over likely text columns
            • Resolves "shoes" → "Running shoes" type cases
            • Plugs into sketch's value-hole scoring
            • Session-cached

Stage 4 — Interactive top-K disambiguation UI    (C.MISP, corroborated E)
            ~60 lines + endpoint
            • UI shows alternatives when top-1/top-2 confidence within 10%
            • User click → POST /api/teach → history.db
            • Next /api/train picks it up automatically

Bonus  — Syntactic Error Rate metric             (G)
            ~15 lines
            • Counters at /api/health: parse_errors, validation_failures,
              execution_errors. Useful for measuring per-stage impact.

Total: ~810 lines + tests. No new heavy deps. Image stays ~300 MB.
       Self-sustaining loop closed via interactive corrections.
```

---

## What was rejected (and why)

| Idea | Source | Why rejected |
|---|---|---|
| LLM in hot path | A, D, much of C | Violates core constraint — entire reason for this branch |
| Fine-tuning / LoRA / RL training | A, D | Requires GPUs + training pipeline + base LLM |
| Synthetic data via LLM | A, D, C-GRAPPA | Requires LLM for generation |
| Graph encoders / RAT-attention | C-RAT-SQL | Heavy ML, training-required |
| BERT / Transformer encoders | C, G | Adds 200+ MB image + training step |
| Few-shot prompting / CoT / consistency decoding | A, D | LLM-only |
| Hand-curated hints (T1b raw form) | A | Reshaped as history-driven (auto-captured via MISP) |
| Sequence-to-sequence neural decoder | C-Seq2SQL, F | We don't train neural nets |

---

## Already shipped on `feature/self-sustaining-nl2sql`

Branch contains the LLM-free baseline plus three early refinements:

- **Commit `35a378e`**: initial self-sustaining nl2sql
  - FastAPI service, sqlglot validator, read-only Postgres executor
  - TF-IDF intent classifier + LinearSVC, retrains from `history.db`
  - rapidfuzz + WordNet schema matcher
  - Template SQL builder via sqlglot AST
  - 45 tests pass

- **Commit `1bf17db`**: implicit values, cross-table WHERE, "total X" intent
  - `app/nlp/implicit.py` — unquoted-literal detection (`org swaraj`)
  - Cross-table WHERE with auto-join
  - Regex+seed override for `total <plural>` → count
  - 54 tests pass

The four-stage plan above is what comes next.

---

## Decision history

- After Paper A: considered T1a + T1b. Reshaped T1b as history-driven.
- After Paper B: switched recommendation to "sketch + repair as foundation".
  This is the version that has remained stable.
- After Paper C: added MISP + execution-guided to the plan.
- After Paper D: refined the scoring function to multi-component.
- After Papers E, F, G: no plan changes; corroborations only.

The recommendation has been **stable for 4 papers** (since Paper D). At
this point, additional surveys are unlikely to change the plan unless
they introduce a fundamentally novel mechanism not seen in A–G (e.g.,
constraint solving, formal verification, type inference, retrieval-
augmented sketch synthesis).

---

## UI wireframes — post-implementation

ASCII sketches of every screen the user touches once the four-stage plan
ships. The current static page (`static/index.html`) only covers the
"Main query" and "Single confident result" screens; everything else is
new in Stages 2 / 4 / Bonus.

### Pipeline flow (what happens behind each "Run")

```
                    ┌──────────────────────────┐
   user question →  │  preprocess + extract    │
                    │  values + implicit hints │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  intent classifier       │
                    │  (TF-IDF + LinearSVC)    │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  schema matcher          │  ← rapidfuzz + WordNet
                    │  + DB content lookup     │  ← Stage 3 (Paper A)
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  build SKETCH (typed     │  ← Stage 1 (Paper B)
                    │  holes + NL hints)       │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  type inhabitation       │  ← enumerate fillers
                    │  → top-K completions     │     score each
                    └────────────┬─────────────┘
                                 │
                          confidence ≥ θ ?
                          ┌──────┴──────┐
                         yes            no
                          │             │
                          │   ┌─────────▼──────────┐
                          │   │  REPAIR LOOP       │  ← Stage 1 (Paper B)
                          │   │  fault localize    │
                          │   │  → tactic          │
                          │   │  → retry (≤5)      │
                          │   └─────────┬──────────┘
                          │             │
                          └─────┬───────┘
                                │
                    ┌───────────▼──────────────┐
                    │  execute top-K read-only │  ← Stage 2 (Paper C)
                    │  + multi-component score │  ← Stage 2 (Paper D)
                    │  format / exec / result  /
                    │  / length                │
                    └───────────┬──────────────┘
                                │
                  top-1 unique winner?
                  ┌─────────────┴─────────────┐
                 yes                          no (ties or low conf)
                  │                            │
        ┌─────────▼────────┐         ┌─────────▼─────────┐
        │ render result +  │         │ MISP top-K UI     │  ← Stage 4 (Paper C)
        │ feedback buttons │         │ user picks one    │     confirmed by E
        └─────────┬────────┘         └─────────┬─────────┘
                  │                            │
                  └──────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  log to history.db       │  ← retrain corpus
                    │  (q, sql, picked, score) │     for next /api/train
                    └──────────────────────────┘
```

### Screen 1 — Main query (today; minor additions)

```
┌─ NL2SQL ─────────────── db: tmt-demo · 93 tables · trained · [Retrain] [⚙] ─┐
│                                                                             │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ Ask in English, e.g. "How many active employees does each client have?" │ │
│ │                                                                         │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│ [ Run ]  [ Show SQL only ]  🎙   ☑ Show generated SQL          status…      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Screen 2 — Top-K disambiguation (Stage 4, MISP)

Triggered when top-1 and top-2 confidence are within ~10 %, OR when the
user clicks "show alternatives" on the result screen.

```
┌─ I have 3 ways to read this — pick one ───────────────────────────────────┐
│                                                                           │
│ "Give me total users belongs to org swaraj"                               │
│                                                                           │
│  ┌─ ① count of users in Organization 'swaraj' ── 78% ─────── [ Use ] ──┐ │
│  │ SELECT COUNT(*) FROM "UserOrg"                                       │ │
│  │   JOIN "Organization" ON "UserOrg"."OrganizationId"                  │ │
│  │                       = "Organization"."id"                          │ │
│  │   WHERE "Organization"."name" = 'swaraj'                             │ │
│  │ ▸ ran in 12 ms · returns 1 row                                       │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ┌─ ② list of users in Organization 'swaraj' ── 71% ─────── [ Use ] ──┐ │
│  │ SELECT "User"."id", "User"."name", "User"."email"                    │ │
│  │   FROM "UserOrg"                                                     │ │
│  │   JOIN "User"         ON "UserOrg"."UserId" = "User"."id"            │ │
│  │   JOIN "Organization" ON …                                           │ │
│  │   WHERE "Organization"."name" = 'swaraj' LIMIT 500                   │ │
│  │ ▸ ran in 18 ms · returns 47 rows                                     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ┌─ ③ count of UserOrg rows ─────────────────── 64% ─────── [ Use ] ──┐ │
│  │ SELECT COUNT(*) FROM "UserOrg"                                       │ │
│  │ ▸ rejected: missing Organization filter                              │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  Picking one teaches the system. Next time it'll prefer your choice.      │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Screen 3 — Single confident result (with feedback)

Shown when the top candidate is a clear winner (margin ≥ 10 % to #2,
executes cleanly). Adds a feedback row at the bottom that escalates to
Screen 2 on click.

```
┌─ Result ──────────────────────────────────────────────────────────────────┐
│ EXPLANATION                                                               │
│ COUNT(*) on "UserOrg" joined to "Organization", filtered by name='swaraj' │
├───────────────────────────────────────────────────────────────────────────┤
│ SQL                                                                       │
│ SELECT COUNT(*) AS "count"                                                │
│ FROM "UserOrg"                                                            │
│   INNER JOIN "Organization" ON "UserOrg"."OrganizationId"                 │
│                              = "Organization"."id"                        │
│ WHERE "Organization"."name" = 'swaraj'                                    │
├───────────────────────────────────────────────────────────────────────────┤
│ RESULTS                                                                   │
│  count                                                                    │
│  ─────                                                                    │
│   478                                                                     │
│                                                                           │
│ 1 row · 12 ms · tables: UserOrg, Organization                             │
│ intent: count · confidence: 89%                                           │
│                                                                           │
│ Was this right?  [ ✓ Yes ]  [ ✗ No, show alternatives ]                   │
└───────────────────────────────────────────────────────────────────────────┘
```

### Screen 4 — Health & quality metrics (Bonus, Paper G's SER)

A small dashboard view at `/health` (or under the ⚙ panel). Auto-refreshes.

```
┌─ Health & Quality ────────────────────────────────────────────────────────┐
│                                                                           │
│ DATABASE                                                                  │
│   Active: tmt-demo · 93 tables · last regenerated 2 days ago              │
│                                                                           │
│ MODEL                                                                     │
│   local-rule + tfidf · trained from 56 seeds + 218 history rows           │
│   Last trained: 2026-05-03 · [ Retrain now ]                              │
│                                                                           │
│ QUALITY (last 7 days, 1,243 queries)                                      │
│   ┌─────────────────────────────────────────────────┐                     │
│   │ Successfully executed       ██████████████ 91.3%│                     │
│   │ Returned non-empty result   █████████████  86.1%│                     │
│   │ User accepted top-1         ████████████   79.2%│                     │
│   │ User picked from top-K      ██             11.8%│                     │
│   │ Failed: validation error    ▌              2.4% │                     │
│   │ Failed: parse error         ▏              0.7% │                     │
│   │ Failed: execution error     ▏              0.6% │                     │
│   └─────────────────────────────────────────────────┘                     │
│                                                                           │
│ TOP REPAIR TACTICS APPLIED                                                │
│   add_join          412   (33%)                                           │
│   swap_aggregate    187   (15%)                                           │
│   add_predicate      94    (8%)                                           │
│   change_table       42    (3%)                                           │
│                                                                           │
│ AVG LATENCY                                                               │
│   end-to-end:  68 ms     · sketch+repair: 41 ms                           │
│   exec+score:  19 ms     · DB content lookup: 8 ms                        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Screen 5 — Recent queries (existing, gains new fields)

Already in the UI, but now records the user's choice so retrains can
weight it.

```
┌─ Recent queries ──────────────────────────────────────────────────────────┐
│                                                                           │
│ ✓ Give me total users belongs to org swaraj                               │
│   2026-05-03 10:14 · 1 row · 12 ms · count · 89% · accepted top-1         │
│                                                                           │
│ ✓ list users of org swaraj                                                │
│   2026-05-03 10:09 · 47 rows · 18 ms · list · 71% · picked alt #2         │
│                                                                           │
│ ✗ tell me about the thing                                                 │
│   2026-05-03 10:01 · 0 rows · —    · list · 12% · could not build         │
│                                                                           │
│ ✓ top 5 clients by paymentTerm                                            │
│   2026-05-03 09:58 · 5 rows · 9 ms  · top   · 92% · accepted top-1        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### API surface (additions)

| Method | Path | Purpose | Stage |
|---|---|---|---|
| `POST` | `/api/ask` | Existing — gains `candidates: list[…]` field when ambiguous | 1 |
| `POST` | `/api/teach` | Record user's pick from top-K | 4 |
| `POST` | `/api/feedback` | "Yes / No" thumbs on a single-result screen | 4 |
| `GET`  | `/api/metrics` | SER + repair-tactic counters for Screen 4 | Bonus |
| `POST` | `/api/train` | Existing — now also weights teach/feedback rows | 4 |

### Why these screens, in this order

- **Screen 1** stays the calm default. The user types and runs.
- **Screen 3** is what they see *most* of the time (high confidence).
- **Screen 2** is the *learning surface* — the one place where ambiguity is
  surfaced honestly and where each interaction makes the system better.
- **Screen 4** is observability — supports the operator decision "is this
  worth retraining now?" without leaving the UI.
- **Screen 5** is the audit log; gains the `picked` column so we can see
  which alternates were chosen historically.

---

## Will this work? — calibrated probability assessment

For a fixed schema with curated synonyms (which the schema DSL provides),
the plan should hit the same accuracy ceiling LLMs do on common
operational queries — deterministic output, ~300 MB image, zero external
API calls.

**Calibrated bet (without LLM build oracle)**:

| Outcome | Probability |
|---|---|
| Substantially better than today (graceful failure instead of empty SQL) | ~85 % |
| 75 %+ accuracy on common queries within 2 weeks of dogfooding | ~60 % |
| Matches LLM accuracy on the full range of queries | ~15 % |
| Complete failure / has to be ripped out | ~5 % |

**What it WON'T do** (no symbolic system can, no matter how layered):

| Query type | Example |
|---|---|
| Multi-hop reasoning | "customers who bought X but not Y in Q1" |
| Implicit aggregation logic | "best month for revenue" (ARGMAX-style) |
| Domain-specific math | "churn rate", "MoM growth" |
| Novel phrasings outside the trained classifier | "wildly different from typical" |
| Free-text content search | "clients with notes mentioning X" |

The realistic accuracy target is **75–85 % on common operational queries**
*if* the schema gets annotated with synonyms.

---

## Improvement analysis — where wins actually come from

Stepping outside the four-stage plan: **what's the highest-leverage
improvement we can make?** Best estimate of where failures originate on
real-world schemas like `tmt_schema.yml` (93+ tables):

```
Schema annotation gaps        ████████████████████████████  ~40 %
Intent classifier weakness    ██████████████                ~20 %
Missing query types (GROUP/   ████████████                  ~17 %
  HAVING/window/CTE/UNION)
Value resolution              ████████                      ~12 %
Repair-loop absence           ██████                         ~8 %
Latency / UX                  ██                             ~3 %
```

**The four-stage plan addresses ~23 % (repair, value resolution, UX).
The remaining ~77 % is pre-algorithmic**: better schema data, broader
intent coverage, and more diverse training data.

### Five biggest improvements, ranked by ROI

| # | Improvement | Effort | Expected accuracy delta | Source |
|---|---|---|---|---|
| 1 | **Schema annotation tooling** (auto-suggest synonyms, descriptions, sample-distinct values) | 4 h | +15–25 % | this analysis |
| 2 | **Sentence-embedding intent classifier** (replace TF-IDF char n-grams; use fastembed ONNX MiniLM) | 3 h | +5–8 % | this analysis (informed by Paper C) |
| 3 | **GROUP BY / HAVING / time-bucket intents** (`X by Y`, `per client`, `for each month`) | 3 h | +10–15 % | this analysis |
| 4 | **Time-range expression parser** (`last week`, `this month`, `Q1`, `between A and B`, `YTD`) | 2 h | +5–10 % | this analysis |
| 5 | **Better failure diagnosis** (pinpoint sketch hole that broke; suggest actionable fix) | 1 h | UX / trust, not accuracy | Paper B + this analysis |

### Three architectural ideas worth considering (bigger swings)

**A. Compiled query templates for the most common patterns**

Skip the sketch+repair entirely for super-common shapes. Pattern-match
the question against a regex bank → if hit, fill slots and emit SQL
directly. Hits in <1 ms. Most production NL2SQL systems work this way.
The sketch+repair handles the long tail.

**B. Schema introspection 2.0 — distinct-value sampling**

When introspecting a new DB, also sample 50 distinct values per text
column. Store in DSL as `sample_values:`. Two payoffs:
- Implicit synonym discovery — `marital_status` with samples
  `['single', 'married']` makes "single men" findable
- Type refinement — distinguishes free-text name columns from enum
  status columns from ID columns

**C. Active-learning prioritization**

Rank historical queries for retraining:
- Queries where user picked from top-K (high signal: ambiguity that got resolved)
- Queries that failed and were rephrased successfully
- Queries that went from low-confidence → high-confidence over time
- Decay weights for old queries (schema may have evolved)

### What we'd skip or defer

| Item | Why skip |
|---|---|
| Hand-curated hints (Paper A.T1b) | History-driven loop captures the same info organically |
| Graph-based schema encoding (RAT-SQL) | Heavy ML, marginal gain on annotated schemas |
| Multi-hop reasoning ("X but not Y") | Requires LLM-style reasoning; ~5 % of queries; document as known limit |
| Free-text search (FTS over descriptions) | Add only if description-heavy columns exist |
| Multi-dialect support | YAGNI until non-Postgres target appears |
| RL training, fine-tuning | LLM-only, not for us |

### The two-question gate

Before any code:

1. **What does "good enough" mean?** "Reduce SQL-writing time for the team's recurring queries" → top-5 list above gets you there. "Answer arbitrary questions a non-technical user types" → always hits the LLM ceiling.
2. **How many minutes/week on schema annotation?** Zero → ceiling ~60 %. 30 min/week → 80–85 % within a month. **Or: use an LLM at build time** (next section) → annotation cost drops to ~$0.30 once.

---

## LLM as build-time oracle (the smart pivot)

**The constraint was "no LLM in the *hot path*"** — using an LLM as an
**offline build-and-train tool** is a completely different category. It
addresses the biggest bottleneck (schema annotation) without violating
the runtime constraint.

This is the SQL-PaLM data-engineering playbook, but unlike SQL-PaLM, **we
keep inference symbolic**. The LLM never enters production.

### Six offline pipelines

| # | Pipeline | What it does | Cost | Frequency |
|---|---|---|---|---|
| 1 | `tools/annotate_schema.py` | Auto-generate `description:` + `synonyms:` per column from name + sample DB values + FK context | $0.10–$0.30 | On schema change |
| 2 | `tools/generate_seed.py` | Replace 60 hand-written training examples with 500–1000 LLM-generated diverse phrasings, balanced across intents | $0.05–$0.10 | On schema change |
| 3 | `tools/generate_benchmark.py` | Produce 50–100 (question, expected_sql) pairs as ground-truth held-out test set | $0.20 | On schema change |
| 4 | `tools/active_label.py` | When classifier confidence < 0.3, ask LLM once, cache label, retrain on combined corpus | ~$0.001/query | Continuous (rare) |
| 5 | `tools/diagnose_failure.py` | For queries we miss, ask LLM "what's wrong"; use the diagnosis to design new repair tactics symbolically | $0.005/failure | Weekly batch |
| 6 | `tools/generate_tests.py` | Per new feature, generate edge-case test inputs covering the new intent or repair tactic | $0.10/feature | Per feature |

**Lifetime total over 6 months of dev + production: under $20.**

### Architectural separation

The LLM never enters production. Deployable image stays unchanged — no
`openai` package, no API key, no network calls.

```
docker-nl2sql/
├── app/                     ← runtime, LLM-free, ~300 MB image
│   ├── builder.py
│   ├── generator.py
│   └── ...
└── tools/                   ← dev/CI only, may call OpenAI
    ├── annotate_schema.py
    ├── generate_seed.py
    ├── generate_benchmark.py
    ├── active_label.py
    ├── diagnose_failure.py
    └── .cache/              ← gitignored, holds LLM responses
```

`tools/` requires `OPENAI_API_KEY` set in dev shell. Production never sees it.

### Guardrails

| Risk | Guardrail |
|---|---|
| LLM hallucinates wrong synonyms / descriptions | Annotations land as PR-style diff; accept/reject per column. Never auto-applied. |
| Schema metadata leaks to OpenAI | Document explicitly. `--exclude-tables` flag for sensitive schemas. Optional redaction mode (replace real names with `tableA`, map back after annotation). |
| Determinism / reproducibility | Pin model (`gpt-4o-2024-08-06`), `temperature=0`, cache all responses to disk under `tools/.cache/`. |
| Annotation drift between runs | Annotations committed to git. Re-runs only update *new* tables/columns by default; existing requires explicit `--overwrite`. |
| Test set contaminates training | Benchmark held out; never used in seed corpus. |
| Cost runaway | Hard daily budget cap in OpenAI dashboard ($1 is more than enough). |

### Updated plan with LLM-as-build-oracle

Order changes meaningfully — annotation moves first because it's now cheap:

| Order | Item | LLM-powered? | Effort | Impact |
|---|---|---|---|---|
| **0** | Build `tools/annotate_schema.py` | ✅ | 2 h | Foundation for everything |
| **1** | Build `tools/generate_seed.py` + `generate_benchmark.py` | ✅ | 2 h | Measurement + training data |
| **2** | Run pipelines, accept annotations, establish baseline | ✅ | 1 h | "today we get N/100" |
| **3** | Sketch + repair loop (Paper B) | symbolic runtime | 8 h | +10–15 % accuracy |
| **4** | GROUP BY / HAVING / time-range coverage | symbolic | 5 h | +15–20 % accuracy |
| **5** | Sentence-embedding intent classifier | symbolic runtime; LLM may inform seeds | 3 h | +5–8 % |
| **6** | DB content matching (Paper A.T1a) | symbolic | 3 h | +5–8 % |
| **7** | Active labeling pipeline | ✅ batch | 2 h | self-improving loop |
| **8** | Execution-guided + multi-component scoring (Stages 2 + Bonus) | symbolic | 2.5 h | +3–5 % |
| **9** | MISP top-K UI | symbolic | 1.5 h | UX + training signal |
| **10** | Failure-diagnosis pipeline (weekly) | ✅ batch | 1 h | continuous improvement |

**Total effort**: ~31 hours. **Runtime LLM cost: $0.** **Build LLM cost: ~$20 lifetime.**

### Probability of success — updated

| Outcome | Without build LLM | **With build LLM** |
|---|---|---|
| Substantially better than today | 85 % | **95 %** |
| 75 %+ accuracy within 2 weeks | 60 % | **85 %** |
| 85 %+ accuracy within a month | 30 % | **65 %** |
| Matches LLM on full range | 15 % | **25 %** |
| Complete failure | 5 % | **< 2 %** |

The schema-annotation gap was the biggest bottleneck. Closing it with an
LLM at build time is the right answer.

### Concrete next step

Build the smallest end-to-end LLM tool first to validate the approach:
**`tools/annotate_schema.py`** on a 5-table slice of the real schema
(Client, Organization, Employee, Tour, Invoice). ~1 hour. We see:

- Does the LLM produce reasonable synonyms / descriptions?
- How much does the matcher improve on a benchmark with vs without
  annotations?
- Is the cost what was estimated?

If it works → build the rest. If it doesn't → we know the bottleneck
before committing more time.

**Prerequisites**:
1. `OPENAI_API_KEY` in dev shell or `.env`
2. OK to send schema metadata (table/column names) to OpenAI
3. Trial slice of 5 tables agreed
