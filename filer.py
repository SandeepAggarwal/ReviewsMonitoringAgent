import json
import uuid
import numpy as np
from openai import OpenAI
from sentence_transformers import SentenceTransformer

import db
from config import (
    AUTO_ATTACH_DIST, JUDGE_DIST, PROMOTE_AT, ARCHIVE_AFTER_DAYS,
    EMBED_MODEL, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY,
)

_llm = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


class Embedder:
    def __init__(self, model_name=EMBED_MODEL):
        self.model = SentenceTransformer(model_name)

    def embed(self, text):
        return self.model.encode(
            text or "", normalize_embeddings=True
        ).tolist()


_JUDGE_PROMPT = """You decide if two customer reviews describe the SAME
underlying issue.

Existing cluster title: "{title}"
Existing example summary: "{example}"

New review summary: "{new_summary}"
New review quote: "{new_quote}"

Return JSON: {{"same_issue": true|false, "reason": "..."}}
"""

_NAME_PROMPT = """You are naming a cluster of customer reviews for a
custom sticker printing service.

Reviews so far:
{reviews}

Return JSON: {{"title": "<=8 words", "suggested_action": "one sentence"}}
"""


class Filer:
    def __init__(self, embedder):
        self.embedder = embedder

    def file(self, review, extract):
        vec = self.embedder.embed(extract["summary"])
        db.save_embedding(review["review_id"], vec)

        candidates = db.nearest_clusters(
            vec, extract["stage"], extract["issue_type"]
        )
        if not candidates:
            return self._new_cluster(review, extract)

        best = candidates[0]
        dist = float(best["dist"])

        if dist < AUTO_ATTACH_DIST:
            return self._attach(best, review)

        if dist < JUDGE_DIST and self._same_issue(extract, best):
            return self._attach(best, review)

        return self._new_cluster(review, extract)

    def promote_and_archive(self):
        return {
            "promoted": db.promote_candidates(PROMOTE_AT),
            "archived": db.archive_stale_candidates(ARCHIVE_AFTER_DAYS),
        }

    # ----- internals -----

    def _same_issue(self, extract, cluster):
        sample = db.get_cluster_sample_review(cluster["cluster_id"])
        prompt = _JUDGE_PROMPT.format(
            title=cluster["title"],
            example=(sample or {}).get("summary", ""),
            new_summary=extract["summary"],
            new_quote=extract.get("evidence_quote", ""),
        )
        resp = _llm.chat.completions.create(
            model=LLM_MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return bool(json.loads(resp.choices[0].message.content).get("same_issue"))

    def _attach(self, cluster, review):
        db.attach_to_cluster(cluster["cluster_id"], review["review_id"])
        if cluster["status"] == "resolved":
            db.update_cluster(cluster["cluster_id"], {
                "status": "confirmed",
                "regression": True,
                "resolved_at": None,
            })
            return {"action": "reopened", "cluster_id": cluster["cluster_id"]}
        return {"action": "attached", "cluster_id": cluster["cluster_id"]}

    def _new_cluster(self, review, extract):
        title, action = self._name_cluster(extract)
        cid = str(uuid.uuid4())
        db.create_cluster({
            "cluster_id": cid,
            "title": title,
            "stage": extract["stage"],
            "issue_type": extract["issue_type"],
            "status": "candidate",
            "regression": False,
            "suggested_action": action,
        })
        db.attach_to_cluster(cid, review["review_id"])
        return {"action": "new", "cluster_id": cid}

    def _name_cluster(self, extract):
        prompt = _NAME_PROMPT.format(
            reviews=json.dumps([extract], indent=2, default=str)
        )
        resp = _llm.chat.completions.create(
            model=LLM_MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("title", "Untitled"), data.get("suggested_action", "")