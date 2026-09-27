import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL    = os.getenv("LLM_MODEL", "mlx-community/Qwen-3.5-4B-8bit")

# --- Embeddings ---
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# --- Taxonomy ---
STAGES = [
    "pre_order", "design_upload", "proof", "checkout",
    "print", "ship", "delivery", "support",
]

ISSUE_TYPES = [
    "bug", "ux", "delay", "quality",
    "pricing", "policy", "feature_request", "praise",
]

# --- Scoring ---
WEIGHTS = {
    "revenue": 0.30,
    "refunds": 0.20,
    "churn":   0.20,
    "rating":  0.15,
    "sla":     0.15,
}

AUTO_ATTACH_DIST   = 0.15
JUDGE_DIST         = 0.30
PROMOTE_AT         = 3
ARCHIVE_AFTER_DAYS = 30
TREND_MULT         = {"rising": 1.25, "flat": 1.0, "falling": 0.85}
REGRESSION_MULT    = 1.4