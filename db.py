import os
import json
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/sticker_reviews")


def _conn():
    return psycopg.connect(DB_URL, row_factory=dict_row)


# ---------- reads ----------

def get_new_reviews(limit=500):
    sql = """
        SELECT r.* FROM reviews r
        LEFT JOIN review_extracts e ON e.review_id = r.review_id
        WHERE e.review_id IS NULL
        ORDER BY r.created_at ASC
        LIMIT %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (limit,))
        return cur.fetchall()


def get_user(user_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def get_order(order_id):
    if not order_id:
        return None
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        return cur.fetchone()


def nearest_clusters(vec, stage, issue_type, limit=1):
    vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
    sql = """
        SELECT c.cluster_id, c.title, c.status, c.regression,
               c.suggested_action,
               MIN(re.embedding <=> %s::vector) AS dist
        FROM review_embeddings re
        JOIN review_clusters rc ON rc.review_id = re.review_id
        JOIN issue_clusters   c ON c.cluster_id = rc.cluster_id
        WHERE c.stage = %s
          AND c.issue_type = %s
          AND c.status IN ('candidate','confirmed','resolved')
        GROUP BY c.cluster_id
        ORDER BY dist
        LIMIT %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (vec_str, stage, issue_type, limit))
        return cur.fetchall()


def get_cluster(cluster_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM issue_clusters WHERE cluster_id = %s",
                    (cluster_id,))
        return cur.fetchone()


def get_cluster_reviews(cluster_id):
    sql = """
        SELECT r.* FROM reviews r
        JOIN review_clusters rc ON rc.review_id = r.review_id
        WHERE rc.cluster_id = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (cluster_id,))
        return cur.fetchall()


def get_cluster_sample_review(cluster_id):
    sql = """
        SELECT e.* FROM review_extracts e
        JOIN review_clusters rc ON rc.review_id = e.review_id
        WHERE rc.cluster_id = %s
        LIMIT 1
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (cluster_id,))
        return cur.fetchone()


def get_active_clusters():
    sql = """
        SELECT * FROM issue_clusters
        WHERE status IN ('candidate','confirmed')
        ORDER BY last_seen DESC
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def get_top_clusters(limit=20):
    sql = """
        SELECT * FROM issue_clusters
        WHERE status IN ('candidate','confirmed')
        ORDER BY score DESC NULLS LAST
        LIMIT %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (limit,))
        return cur.fetchall()


def count_cluster_reviews_since(cluster_id, since):
    sql = """
        SELECT COUNT(*) AS n FROM review_clusters rc
        JOIN reviews r ON r.review_id = rc.review_id
        WHERE rc.cluster_id = %s AND r.created_at >= %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (cluster_id, since))
        return cur.fetchone()["n"]


# ---------- writes ----------

def save_extract(review_id, data):
    sql = """
        INSERT INTO review_extracts
            (review_id, stage, issue_type, is_bug, is_feature_request,
             severity, summary, evidence_quote, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (review_id) DO UPDATE SET
            stage = EXCLUDED.stage,
            issue_type = EXCLUDED.issue_type,
            is_bug = EXCLUDED.is_bug,
            is_feature_request = EXCLUDED.is_feature_request,
            severity = EXCLUDED.severity,
            summary = EXCLUDED.summary,
            evidence_quote = EXCLUDED.evidence_quote,
            confidence = EXCLUDED.confidence
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (
            review_id,
            data.get("stage"),
            data.get("issue_type"),
            data.get("is_bug", False),
            data.get("is_feature_request", False),
            data.get("severity"),
            data.get("summary"),
            data.get("evidence_quote"),
            data.get("confidence"),
        ))


def save_embedding(review_id, vec):
    vec_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
    sql = """
        INSERT INTO review_embeddings (review_id, embedding)
        VALUES (%s, %s::vector)
        ON CONFLICT (review_id) DO UPDATE SET embedding = EXCLUDED.embedding
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (review_id, vec_str))


def create_cluster(data):
    sql = """
        INSERT INTO issue_clusters
            (cluster_id, title, stage, issue_type, status,
             regression, suggested_action)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (
            data["cluster_id"], data["title"], data["stage"],
            data["issue_type"], data["status"],
            data.get("regression", False),
            data.get("suggested_action", ""),
        ))


def update_cluster(cluster_id, fields):
    keys = list(fields.keys())
    sets = ", ".join(f"{k} = %s" for k in keys)
    sql = f"UPDATE issue_clusters SET {sets} WHERE cluster_id = %s"
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, [fields[k] for k in keys] + [cluster_id])


def attach_to_cluster(cluster_id, review_id):
    sql = """
        INSERT INTO review_clusters (review_id, cluster_id)
        VALUES (%s, %s) ON CONFLICT DO NOTHING
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (review_id, cluster_id))
        cur.execute(
            "UPDATE issue_clusters SET last_seen = NOW() WHERE cluster_id = %s",
            (cluster_id,),
        )


def promote_candidates(min_reviews):
    sql = """
        UPDATE issue_clusters c
        SET status = 'confirmed'
        WHERE c.status = 'candidate'
          AND (SELECT COUNT(*) FROM review_clusters rc
               WHERE rc.cluster_id = c.cluster_id) >= %s
        RETURNING cluster_id
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (min_reviews,))
        return len(cur.fetchall())


def archive_stale_candidates(days):
    sql = """
        UPDATE issue_clusters
        SET status = 'archived'
        WHERE status = 'candidate'
          AND last_seen < NOW() - INTERVAL '%s days'
        RETURNING cluster_id
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (days,))
        return len(cur.fetchall())


def save_cluster_score(cluster_id, data):
    sql = """
        UPDATE issue_clusters
        SET score = %s, signals = %s, trend = %s
        WHERE cluster_id = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (
            data["score"],
            json.dumps(data["signals"]),
            data["trend"],
            cluster_id,
        ))