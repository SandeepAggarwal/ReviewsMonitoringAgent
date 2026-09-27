import numpy as np
from datetime import datetime, timezone, timedelta

import db
from config import WEIGHTS, TREND_MULT, REGRESSION_MULT


class Scorer:
    def __init__(self, weights=None):
        self.weights = weights or WEIGHTS
        self._norms = {}
        self._per_cluster = {}

    def score_review(self, review, user, order):
        rating = review.get("rating") or 3
        rating_penalty = (5 - rating) / 4

        revenue_at_risk = float(user["ltv"]) if user else 0.0
        refund_dollars = float(order["refund_amount"]) if order else 0.0
        sla = self._sla_breach(order) if order else 0.0

        order_count = user["order_count"] if user else 0
        churn = self._churn_probability(rating, order_count)

        return {
            "rating_penalty": rating_penalty,
            "revenue_at_risk": revenue_at_risk,
            "refund_dollars": refund_dollars,
            "sla_breach": sla,
            "churn_probability": churn,
        }

    def normalize_all(self, clusters):
        buckets = {k: [] for k in ("revenue", "refunds", "churn", "rating", "sla")}
        self._per_cluster = {}
        for c in clusters:
            agg = self._aggregate(c["cluster_id"])
            self._per_cluster[c["cluster_id"]] = agg
            for k in buckets:
                buckets[k].append(agg[k])
        self._norms = {k: sorted(v) for k, v in buckets.items()}

    def score_cluster(self, cluster_id):
        cluster = db.get_cluster(cluster_id)
        agg = self._per_cluster.get(cluster_id) or self._aggregate(cluster_id)
        norms = {
            k: self._pct_norm(agg[k], self._norms.get(k, [agg[k]]))
            for k in agg
        }
        base = sum(self.weights[k] * norms[k] for k in self.weights)
        trend = self._trend(cluster_id)
        score = 100 * base * TREND_MULT[trend]
        if cluster.get("regression"):
            score *= REGRESSION_MULT

        return {
            "score": round(score, 2),
            "signals": agg,
            "trend": trend,
            "regression": bool(cluster.get("regression")),
        }

    # ----- internals -----

    def _aggregate(self, cluster_id):
        rows = db.get_cluster_reviews(cluster_id)
        revenue = refunds = 0.0
        churn, rating, sla = [], [], []
        for r in rows:
            user = db.get_user(r["user_id"]) if r.get("user_id") else None
            order = db.get_order(r["order_id"]) if r.get("order_id") else None
            s = self.score_review(r, user, order)
            revenue += s["revenue_at_risk"]
            refunds += s["refund_dollars"]
            churn.append(s["churn_probability"])
            rating.append(s["rating_penalty"])
            sla.append(s["sla_breach"])
        return {
            "revenue": revenue,
            "refunds": refunds,
            "churn": float(np.mean(churn)) if churn else 0.0,
            "rating": float(np.mean(rating)) if rating else 0.0,
            "sla": float(np.mean(sla)) if sla else 0.0,
        }

    def _sla_breach(self, order):
        sla_days = order.get("sla_days")
        created = order.get("created_at")
        shipped = order.get("shipped_at")
        if not (sla_days and created and shipped):
            return 0.0
        return 1.0 if (shipped - created).days > sla_days else 0.0

    def _churn_probability(self, rating, order_count):
        rating_penalty = (5 - rating) / 4
        repeat_weight = 1.0 if order_count >= 2 else 0.6
        return rating_penalty * repeat_weight

    def _pct_norm(self, value, all_values):
        if not all_values:
            return 0.0
        arr = np.array(all_values)
        return float((arr < value).sum()) / len(arr)

    def _trend(self, cluster_id):
        now = datetime.now(timezone.utc)
        recent = db.count_cluster_reviews_since(
            cluster_id, now - timedelta(days=7)
        )
        prior_total = db.count_cluster_reviews_since(
            cluster_id, now - timedelta(days=14)
        )
        prior = max(prior_total - recent, 0)
        if recent > prior * 1.5 and recent >= 2:
            return "rising"
        if recent < prior * 0.5:
            return "falling"
        return "flat"