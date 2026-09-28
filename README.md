# Reviews Handler Agent

An AI agent that reads customer reviews daily, groups similar issues into clusters, and produces a prioritized digest of bugs, bottlenecks, and feature requests — ranked by revenue at risk, refunds, churn, ratings, and SLA breaches.

Built for a custom sticker printing service. Works with any review source that feeds into Postgres.

---

## What it does

Every day, for each new review:

1. **Extracts** structured meaning (stage, issue type, severity, summary, quote) using a local LLM.
2. **Groups** the review into an existing issue cluster, or opens a new one — using embeddings + an LLM judge for close calls.
3. **Reopens** clusters that were previously marked resolved if a strong match appears again (regression detection).
4. **Scores** every active cluster by revenue at risk, refunds, churn, ratings, and SLA breaches.
5. **Prints / posts** a digest: top issues, rising trends, regressions, and stage health.

Every suggestion is human-reviewed. The agent never auto-acts.

---

## Architecture

```
        ┌──────────────────────────────────────────────┐
        │              pipeline.py                     │
        │   run_daily()  ·  build_digest()             │
        └───────┬──────────────┬──────────────┬────────┘
                │              │              │
                ▼              ▼              ▼
          ┌─────────┐    ┌─────────┐    ┌──────────┐
          │ Reader  │    │  Filer  │    │  Scorer  │
          └────┬────┘    └────┬────┘    └────┬─────┘
               │              │              │
               └──────┬───────┴──────┬───────┘
                      ▼              ▼
                 ┌─────────┐   ┌──────────┐
                 │  db.py  │   │config.py │
                 └─────────┘   └──────────┘
```

| File | Role |
|---|---|
| `reader.py` | LLM extraction: review text → structured JSON |
| `filer.py` | Embeddings + clustering + regression detection |
| `scorer.py` | Priority formula across 5 signals |
| `db.py` | All Postgres access (plain functions) |
| `config.py` | Taxonomy, weights, thresholds (loaded from `.env`) |
| `pipeline.py` | Orchestration + digest generation |

---

## Prerequisites

- macOS (tested on Apple Silicon, M-series)
- Python 3.11+
- Homebrew
- PostgreSQL 17 with pgvector
- A local MLX LLM server (OpenAI-compatible API)

---

## Setup

### 1. Install PostgreSQL 17 with pgvector

Do **not** use PostgreSQL 16 — Homebrew's pgvector formula doesn't support it and you'll have to compile from source.

```bash
brew install postgresql@17
brew services start postgresql@17

# Add postgresql@17 to PATH if not already
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Install pgvector (works out of the box on 17)
brew install pgvector
```

Verify:

```bash
psql --version           # should print 17.x
```

### 2. Create the database and enable pgvector

```bash
createdb sticker_reviews
psql sticker_reviews -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql sticker_reviews -c "\dx"    # should list 'vector'
```

### 3. Load the schema

```bash
psql sticker_reviews -f schema.sql
```

### 4. Install the local LLM server (MLX)

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen-3.5-4B-8bit --port 8080 --max-tokens 1024 --chat-template-args '{"enable_thinking":false}'
```

Leave this running in a separate terminal. Verify it's up:

```bash
curl http://localhost:8080/v1/models
```

You should see the model name in the response. That exact string must match `LLM_MODEL` in your `.env`.

### 5. Clone and set up the project

```bash
git clone <your-repo-url>
cd ReviewsHandlerAgent

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 6. Create `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Database
DATABASE_URL=postgresql://YOUR_MAC_USERNAME@localhost:5432/sticker_reviews

# Local LLM (MLX server)
LLM_BASE_URL=http://localhost:8080/v1
LLM_API_KEY=not-needed
LLM_MODEL=mlx-community/Qwen-3.5-4B-8bit

# Embeddings (runs locally on CPU)
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Optional: post digest to Slack
SLACK_WEBHOOK_URL=
```

Replace `YOUR_MAC_USERNAME` with `whoami`.

### 7. Verify the connection

```bash
python -c "import db; print(db.get_active_clusters())"
```

Expected: `[]` (empty list).

---

## Running

Make sure the MLX server is running in one terminal:

```bash
mlx_lm.server --model mlx-community/Qwen-3.5-4B-8bit --port 8080 --max-tokens 1024 --chat-template-args '{"enable_thinking":false}'
```

Then in another terminal:

```bash
source venv/bin/activate
python pipeline.py
```

First run with an empty database prints:

```
[run_daily] new reviews: 0
[run_daily] lifecycle: {'promoted': 0, 'archived': 0}
```

That means the whole chain works.

To load mock data and watch the agent actually process reviews:

```bash
python seed.py --reset
python pipeline.py
```

---

## Daily automation

Add to crontab (`crontab -e`):

```cron
0 7 * * * cd /path/to/ReviewsHandlerAgent && /path/to/venv/bin/python pipeline.py >> /var/log/review_agent.log 2>&1
```

The digest is posted to Slack if `SLACK_WEBHOOK_URL` is set, otherwise printed to stdout (which cron captures in the log).

---

## How prioritization works

Each review contributes to five signals:

| Signal | Source |
|---|---|
| Revenue at risk | User LTV |
| Refunds | Order refund amount |
| Churn | Derived from rating + repeat-purchase history |
| Ratings | Rating penalty (1→1.0, 5→0.0) |
| SLA breach | Order timestamps vs. promised SLA |

Each cluster aggregates these across its reviews. Signals are percentile-normalized across all active clusters, then combined with weights from `config.py`:

```python
score = 100 * (
    0.30 * norm(revenue)
  + 0.20 * norm(refunds)
  + 0.20 * churn_score
  + 0.15 * rating_penalty
  + 0.15 * sla_breach_rate
) * trend_multiplier * regression_multiplier
```

Trend multiplier: rising `1.25`, flat `1.0`, falling `0.85`.
Regression multiplier: `1.4` for reopened clusters.

Tune the weights in `config.py` after a couple of weeks of output.

---

## How overlap detection works

When a new review arrives:

1. Embed its summary.
2. Query Postgres for the closest clusters **in the same stage + issue type**.
3. Compare cosine distances:
   - `< 0.15` → auto-attach to the nearest cluster.
   - `0.15 – 0.30` → ask the LLM "same issue?" and attach or create new.
   - `> 0.30` → create a new candidate cluster.
4. If the matched cluster was `resolved`, reopen it and set `regression = true`.

Candidates with fewer than 3 reviews get archived after 30 days. Candidates with ≥ 3 reviews within 14 days are promoted to `confirmed`.

---

## Regression detection

When a cluster is marked `resolved` by a human:

- Its status becomes `resolved` and `resolved_at` is set.
- Any new review matching it strongly triggers a reopen: status → `confirmed`, `regression = true`, `resolved_at` reset to `NULL`.
- The cluster's priority gets a `1.4×` boost and it appears in the digest under "Regressions".

This lets you see whether a shipped fix actually held.

---

## Tuning

The three knobs that matter most:

**`config.py` → `AUTO_ATTACH_DIST` / `JUDGE_DIST`**
Run on 500 historical reviews. Count how many clusters form. Too many singletons → loosen (raise thresholds). Too few clusters covering distinct problems → tighten.

**`config.py` → `WEIGHTS`**
After two weeks, ask the team: did the top 5 feel right? Adjust one weight at a time.

**`config.py` → `EMBED_MODEL`**
Default `all-MiniLM-L6-v2` is fast and free. If clustering quality feels weak, swap to `BAAI/bge-small-en-v1.5` — same size, better quality.

**`LLM_MODEL`**
`Qwen-3.5-4B-8bit` is fine for POC. Upgrade to a larger model if extraction accuracy matters more than latency.

---

## Troubleshooting

**`extension "vector" is not available`**
You're on PostgreSQL 16. Uninstall it, install PostgreSQL 17, and `brew install pgvector`. See "Uninstall PostgreSQL 16" below.

**`connection to server ... failed: Connection refused`**
Postgres isn't running. `brew services start postgresql@17`.

**MLX server returns 404 on `/v1/chat/completions`**
The server isn't running, or `LLM_BASE_URL` in `.env` doesn't match the port. Default MLX port is 8080.

**Reviews keep creating new clusters**
`AUTO_ATTACH_DIST` is too tight. Raise to `0.20`, rerun on historical data, check the cluster count again.

**Extraction fails often with invalid `stage`**
The local model isn't following the taxonomy. Either upgrade the model (7B+), or add fallback in `Reader.extract()` that coerces invalid values to `"other"`.

---

## Uninstall PostgreSQL 16 (if you had it)

```bash
brew services stop postgresql@16
brew uninstall --force postgresql@16
rm -rf /opt/homebrew/var/postgresql@16
brew cleanup postgresql@16
```

Then remove any `postgresql@16` PATH line from `~/.zshrc`.

---

## Project structure

```
ReviewsHandlerAgent/
├── .env                 # Local config (gitignored)
├── .env.example         # Template
├── schema.sql           # Postgres DDL
├── config.py            # Taxonomy + weights + thresholds
├── db.py                # All Postgres access
├── reader.py            # LLM extraction
├── filer.py             # Clustering + regression
├── scorer.py            # Priority formula
├── pipeline.py          # Orchestration + digest
├── seed.py              # Mock data for local testing
├── requirements.txt
└── README.md
```

---

## Roadmap

- [ ] Add order-event integration (real process mining, not just stage inference)
- [ ] Split-drift detection for clusters that outgrow their original scope
- [ ] Merge near-duplicate clusters automatically
- [ ] Slack interactive actions (mark resolved, reassign owner)
- [ ] Closed-loop tracking: did a fix reduce review volume?

---

## License

MIT
