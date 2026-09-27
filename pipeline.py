import os
import requests
from dotenv import load_dotenv

load_dotenv()

import db
from reader import Reader
from filer import Filer, Embedder
from scorer import Scorer


def run_daily():
    reviews = db.get_new_reviews()
    print(f"[run_daily] new reviews: {len(reviews)}")

    reader = Reader()
    filer = Filer(Embedder())
    scorer = Scorer()

    for r in reviews:
        try:
            extract = reader.extract(r)
        except Exception as e:
            print(f"  ! extract failed {r['review_id']}: {e}")
            continue

        db.save_extract(r["review_id"], extract)
        result = filer.file(r, extract)
        print(f"  {r['review_id']} -> {result['action']} {result['cluster_id']}")

    lifecycle = filer.promote_and_archive()
    print(f"[run_daily] lifecycle: {lifecycle}")

    active = db.get_active_clusters()
    scorer.normalize_all(active)
    for c in active:
        result = scorer.score_cluster(c["cluster_id"])
        db.save_cluster_score(c["cluster_id"], result)

    digest = build_digest()
    _publish(digest)
    return digest


def build_digest():
    top = db.get_top_clusters(limit=20)
    lines = ["# Review Digest", ""]

    lines.append("## Top 5 by priority")
    for c in top[:5]:
        reg = "  ⚠️ REGRESSION" if c.get("regression") else ""
        lines.append(
            f"- **[{c['score']:.0f}] {c['title']}** "
            f"({c['stage']}/{c['issue_type']}){reg}"
        )
        sig = c.get("signals") or {}
        if isinstance(sig, dict):
            lines.append(
                f"  - revenue at risk ${sig.get('revenue', 0):.0f}, "
                f"refunds ${sig.get('refunds', 0):.0f}, "
                f"trend={c.get('trend', 'flat')}"
            )
        if c.get("suggested_action"):
            lines.append(f"  - action: {c['suggested_action']}")
    lines.append("")

    lines.append("## Rising this week")
    rising = [c for c in top if c.get("trend") == "rising"]
    if rising:
        for c in rising:
            lines.append(f"- {c['title']} ({c['stage']})")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Regressions")
    regressions = [c for c in top if c.get("regression")]
    if regressions:
        for c in regressions:
            lines.append(f"- ⚠️ {c['title']} — reopened")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Stage health")
    by_stage = {}
    for c in top:
        s = by_stage.setdefault(c["stage"], {"count": 0, "score": 0.0})
        s["count"] += 1
        s["score"] += c["score"] or 0.0
    for stage, d in sorted(by_stage.items(), key=lambda x: -x[1]["score"]):
        lines.append(
            f"- {stage}: {d['count']} active issues, "
            f"total score {d['score']:.0f}"
        )

    return "\n".join(lines)


def _publish(digest):
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        print(digest)
        return
    try:
        requests.post(url, json={"text": digest}, timeout=10)
        print("[publish] posted to Slack")
    except Exception as e:
        print(f"[publish] slack post failed: {e}")
        print(digest)


if __name__ == "__main__":
    run_daily()