import json
from openai import OpenAI

from config import STAGES, ISSUE_TYPES, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY

_llm = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

BATCH_SIZE = 5  # reviews per LLM call

_EXTRACT_SYSTEM = """You extract structured data from customer reviews
for a custom sticker printing service.

You will receive a JSON array of reviews, each with an "id" and "text".
Return ONLY a JSON object with a single key "results" whose value is an
array of objects — one per input review — in the SAME ORDER as the input.

Each result object must have these keys:
- id: the same id as the input review
- stage: one of {stages}
- issue_type: one of {issue_types}
- is_bug: boolean (true only for clear software/system failures)
- is_feature_request: boolean
- severity: "low" | "medium" | "high"
- summary: <=15 word neutral summary of the issue
- evidence_quote: exact quote from the review supporting the summary
- confidence: 0.0-1.0

Rules:
- Do NOT skip any review. Every input id must appear in the output.
- If the review is pure praise, use issue_type="praise" and summarize
  what they liked.
- If the review mentions multiple stages, pick the one that most drove
  the rating.
- If the text is empty or unintelligible, use stage="support",
  issue_type="other", confidence=0.1.
""".format(stages=STAGES, issue_types=ISSUE_TYPES)


class Reader:
    def __init__(self, model=LLM_MODEL, batch_size=BATCH_SIZE):
        self.model = model
        self.batch_size = batch_size

    def extract_many(self, reviews):
        """
        Extract structured data for a list of reviews.

        Input:  list of review dicts
        Output: dict mapping review_id -> extract dict
        """
        results = {}
        for i in range(0, len(reviews), self.batch_size):
            batch = reviews[i:i + self.batch_size]
            results.update(self._extract_batch(batch))
        return results

    def extract(self, review):
        """Single-review convenience wrapper."""
        return self.extract_many([review]).get(review["review_id"])

    # ----- internals -----

    def _extract_batch(self, batch):
        payload = [
            {"id": r["review_id"], "text": (r["text"] or "").strip()}
            for r in batch
        ]
        user_msg = json.dumps(payload, ensure_ascii=False)

        last_err = None
        for attempt in range(2):
            try:
                resp = _llm.chat.completions.create(
                    model=self.model,
                    response_format={"type": "json_object"},
                    temperature=0,
                    messages=[
                        {"role": "system", "content": _EXTRACT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                )
                raw = json.loads(resp.choices[0].message.content)
                items = raw.get("results", [])
                parsed = self._validate_batch(items, batch)
                if parsed is not None:
                    return parsed
                last_err = ValueError(f"batch {attempt} returned invalid items")
            except Exception as e:
                last_err = e

        # Fallback: process this batch one at a time
        print(f"  ! batch failed ({last_err}); falling back to per-review")
        return self._extract_batch_individual(batch)

    def _validate_batch(self, items, batch):
        """Return dict review_id -> extract, or None if too incomplete."""
        by_id = {item.get("id"): item for item in items if item.get("id")}
        result = {}
        for r in batch:
            rid = r["review_id"]
            item = by_id.get(rid)
            if not item:
                return None  # missing — treat as failure
            cleaned = self._clean(item)
            if not cleaned:
                return None
            result[rid] = cleaned
        return result

    def _extract_batch_individual(self, batch):
        result = {}
        for r in batch:
            for _ in range(2):
                try:
                    resp = _llm.chat.completions.create(
                        model=self.model,
                        response_format={"type": "json_object"},
                        temperature=0,
                        messages=[
                            {"role": "system", "content": _EXTRACT_SYSTEM},
                            {"role": "user",
                             "content": json.dumps([{
                                 "id": r["review_id"],
                                 "text": r["text"] or "",
                             }])},
                        ],
                    )
                    raw = json.loads(resp.choices[0].message.content)
                    items = raw.get("results", [])
                    if items:
                        cleaned = self._clean(items[0])
                        if cleaned:
                            result[r["review_id"]] = cleaned
                            break
                except Exception:
                    pass
        return result

    def _clean(self, item):
        """Coerce to valid schema. Returns None if unusable."""
        stage = item.get("stage")
        issue_type = item.get("issue_type")
        if stage not in STAGES:
            stage = "support"
        if issue_type not in ISSUE_TYPES:
            issue_type = "other"

        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None

        conf = item.get("confidence", 0.5)
        if not isinstance(conf, (int, float)):
            conf = 0.5

        return {
            "stage": stage,
            "issue_type": issue_type,
            "is_bug": bool(item.get("is_bug", False)),
            "is_feature_request": bool(item.get("is_feature_request", False)),
            "severity": item.get("severity") if item.get("severity") in
                        ("low", "medium", "high") else "medium",
            "summary": summary.strip(),
            "evidence_quote": (item.get("evidence_quote") or "").strip(),
            "confidence": float(conf),
        }