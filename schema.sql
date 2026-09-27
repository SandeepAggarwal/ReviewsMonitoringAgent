CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    ltv         NUMERIC DEFAULT 0,
    order_count INT DEFAULT 0,
    signup_date TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    user_id       TEXT REFERENCES users(user_id),
    amount        NUMERIC DEFAULT 0,
    refund_amount NUMERIC DEFAULT 0,
    status        TEXT,
    created_at    TIMESTAMPTZ,
    shipped_at    TIMESTAMPTZ,
    delivered_at  TIMESTAMPTZ,
    sla_days      INT
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id  TEXT PRIMARY KEY,
    user_id    TEXT REFERENCES users(user_id),
    order_id   TEXT REFERENCES orders(order_id),
    rating     INT,
    text       TEXT,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS review_extracts (
    review_id       TEXT PRIMARY KEY REFERENCES reviews(review_id),
    stage           TEXT,
    issue_type      TEXT,
    is_bug          BOOLEAN,
    is_feature_request BOOLEAN,
    severity        TEXT,
    summary         TEXT,
    evidence_quote  TEXT,
    confidence      FLOAT,
    extracted_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_embeddings (
    review_id TEXT PRIMARY KEY REFERENCES reviews(review_id),
    embedding vector(384)
);
CREATE INDEX IF NOT EXISTS review_embeddings_hnsw
    ON review_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS issue_clusters (
    cluster_id       TEXT PRIMARY KEY,
    title            TEXT,
    stage            TEXT,
    issue_type       TEXT,
    status           TEXT DEFAULT 'candidate',  -- candidate|confirmed|resolved|archived
    regression       BOOLEAN DEFAULT FALSE,
    first_seen       TIMESTAMPTZ DEFAULT NOW(),
    last_seen        TIMESTAMPTZ DEFAULT NOW(),
    resolved_at      TIMESTAMPTZ,
    suggested_action TEXT,
    score            FLOAT,
    signals          JSONB,
    trend            TEXT
);

CREATE TABLE IF NOT EXISTS review_clusters (
    review_id  TEXT REFERENCES reviews(review_id),
    cluster_id TEXT REFERENCES issue_clusters(cluster_id),
    added_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (review_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS review_clusters_cluster_idx
    ON review_clusters(cluster_id);