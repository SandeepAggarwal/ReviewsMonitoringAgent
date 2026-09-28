"""
Seed the database with mock users, orders, and reviews.

Run once against a fresh database:
    python seed.py

Then run the pipeline:
    python pipeline.py
"""

import random
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from db import DB_URL

random.seed(42)

NOW = datetime.now(timezone.utc)


# ---------- mock data ----------

USERS = [
    # (user_id, ltv, order_count, signup_days_ago)
    ("u_001", 240.00, 4, 400),
    ("u_002",  85.00, 2, 300),
    ("u_003", 620.00, 9, 700),
    ("u_004",  35.00, 1,  60),
    ("u_005", 180.00, 3, 250),
    ("u_006",  50.00, 1,  40),
    ("u_007", 410.00, 6, 500),
    ("u_008",  95.00, 2, 120),
    ("u_009",  20.00, 1,  25),
    ("u_010", 310.00, 5, 380),
    ("u_011", 150.00, 3, 200),
    ("u_012",  70.00, 1,  90),
    ("u_013", 520.00, 8, 600),
    ("u_014",  45.00, 1,  55),
    ("u_015", 260.00, 4, 320),
    ("u_016", 110.00, 2, 150),
    ("u_017",  30.00, 1,  30),
    ("u_018", 380.00, 6, 480),
    ("u_019", 200.00, 3, 270),
    ("u_020",  60.00, 1,  75),
]


def make_orders():
    orders = []
    oid = 1
    for user_id, _, order_count, signup_days in USERS:
        for _ in range(order_count):
            created = NOW - timedelta(days=random.randint(1, signup_days))
            sla_days = random.choice([5, 7, 7, 10])
            shipped = created + timedelta(days=random.randint(3, 14))
            delivered = shipped + timedelta(days=random.randint(1, 6))

            # ~15% of orders get refunded
            refund = 0.0
            if random.random() < 0.15:
                refund = round(random.uniform(15, 90), 2)

            orders.append({
                "order_id": f"o_{oid:04d}",
                "user_id": user_id,
                "amount": round(random.uniform(20, 150), 2),
                "refund_amount": refund,
                "status": "delivered",
                "created_at": created,
                "shipped_at": shipped,
                "delivered_at": delivered,
                "sla_days": sla_days,
            })
            oid += 1
    return orders


# Each entry: (rating, text, tag)
# tag is only used to control distribution — the pipeline re-derives
# stage/issue_type from the text via the LLM.
REVIEW_TEMPLATES = [
    # --- proof notification (bug) — 6 reviews, should cluster ---
    (1, "I never got an email when my proof was ready. I waited a week and had to email support to check.", "proof_bug"),
    (2, "Proof was ready but I didn't know. No notification, no email, nothing. Order sat idle for days.", "proof_bug"),
    (1, "My order was stuck because I never got the proof approval link. Customer service told me the email bounced but I never received any.", "proof_bug"),
    (2, "There is no notification when the proof is ready. I only found out by logging in randomly.", "proof_bug"),
    (1, "Proof-ready email never arrived. Had to chase support. Please fix this notification.", "proof_bug"),
    (2, "Same issue as before — no email when proof is ready. Lost 4 days on my order.", "proof_bug"),

    # --- file upload UX — 4 reviews ---
    (3, "The file size limit is not shown anywhere. I uploaded a 40MB PNG and it just silently failed.", "upload_ux"),
    (2, "Upload kept failing with no error message. Turns out my file was too big, but nothing told me.", "upload_ux"),
    (3, "No indication of accepted file formats on the upload screen. Had to guess and re-upload twice.", "upload_ux"),
    (3, "The upload screen doesn't show file size limits. Wasted 20 minutes troubleshooting.", "upload_ux"),

    # --- shipping delays — 5 reviews ---
    (2, "Shipping took almost two weeks when I was told 5-7 days. Very disappointing for a small order.", "ship_delay"),
    (1, "My stickers arrived 10 days late. No tracking update for a week. Almost missed my event.", "ship_delay"),
    (2, "Order was marked shipped but tracking didn't update for 8 days. Arrived a week after the promised date.", "ship_delay"),
    (2, "Slow shipping. Missed my deadline. The stickers themselves are fine but the delay hurt.", "ship_delay"),
    (3, "Shipping took longer than expected. Not the product's fault but the carrier was slow.", "ship_delay"),

    # --- cut line preview bug — 3 reviews ---
    (1, "The cut line preview is completely wrong. It shows the cut inside the design, not around it.", "cut_bug"),
    (2, "Cut preview is off. The preview showed a different boundary than what was actually printed.", "cut_bug"),
    (1, "Cut lines in the preview don't match the final product. My stickers came out with the design cut off.", "cut_bug"),

    # --- print quality — 3 reviews ---
    (2, "Colors came out much duller than the preview. The design looked vibrant online but flat in person.", "quality"),
    (1, "Print quality was poor. Edges were fuzzy and the colors bled into each other.", "quality"),
    (2, "The stickers look faded compared to the digital preview. Not what I expected.", "quality"),

    # --- pricing confusion — 2 reviews ---
    (3, "Pricing is confusing. The per-sticker price changes depending on quantity but it's not clear how.", "pricing"),
    (2, "I was charged more than expected at checkout. The pricing breakdown wasn't clear upfront.", "pricing"),

    # --- feature requests — 3 reviews ---
    (4, "Would love to see a bulk upload option. Uploading 20 designs one by one is painful.", "feature"),
    (4, "Please add the ability to save designs as templates for reordering.", "feature"),
    (3, "It would be great to have a sticker shape library instead of only custom cut lines.", "feature"),

    # --- praise — 8 reviews ---
    (5, "Amazing quality! The stickers came out perfect and shipping was fast.", "praise"),
    (5, "Love the product. The design tool was easy to use and the stickers look great.", "praise"),
    (5, "Fantastic service. Support helped me fix my file and the stickers arrived on time.", "praise"),
    (5, "Best sticker printing service I've used. Colors are vibrant and the cut is clean.", "praise"),
    (5, "Super happy with the result. Will definitely order again.", "praise"),
    (4, "Great quality overall. Minor delay in shipping but worth the wait.", "praise"),
    (5, "Perfect. Exactly what I needed for my small business.", "praise"),
    (5, "Very happy. The proof process was smooth and the final product looks great.", "praise"),

    # --- pre-order browsing UX — 2 reviews (no order_id) ---
    (3, "Browsing the site on mobile is a bit clunky. The size options are hard to tap.", "preorder_ux"),
    (4, "Would be nice to see sample images of each material before ordering.", "preorder_ux"),
]


def make_reviews(orders):
    reviews = []
    rid = 1

    # Map user_id -> list of their orders
    by_user = {}
    for o in orders:
        by_user.setdefault(o["user_id"], []).append(o)

    for rating, text, tag in REVIEW_TEMPLATES:
        # Pick a random user
        user_id = random.choice([u[0] for u in USERS])

        # Pre-order reviews have no order_id
        order_id = None
        if tag != "preorder_ux" and by_user.get(user_id):
            order = random.choice(by_user[user_id])
            order_id = order["order_id"]
            created = order["delivered_at"] + timedelta(days=random.randint(0, 5))
        else:
            created = NOW - timedelta(days=random.randint(1, 30))

        reviews.append({
            "review_id": f"r_{rid:04d}",
            "user_id": user_id,
            "order_id": order_id,
            "rating": rating,
            "text": text,
            "created_at": created,
        })
        rid += 1

    return reviews


# ---------- insertion ----------

def reset_tables():
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE review_clusters, issue_clusters,
                         review_embeddings, review_extracts,
                         reviews, orders, users
                CASCADE
            """)
        conn.commit()
    print("Tables truncated.")

def insert_all():
    users = [
        {
            "user_id": u[0],
            "ltv": u[1],
            "order_count": u[2],
            "signup_date": NOW - timedelta(days=u[3]),
        }
        for u in USERS
    ]
    orders = make_orders()
    reviews = make_reviews(orders)

    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # users
            for u in users:
                cur.execute(
                    """
                    INSERT INTO users (user_id, ltv, order_count, signup_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (u["user_id"], u["ltv"], u["order_count"], u["signup_date"]),
                )

            # orders
            for o in orders:
                cur.execute(
                    """
                    INSERT INTO orders
                        (order_id, user_id, amount, refund_amount, status,
                         created_at, shipped_at, delivered_at, sla_days)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (order_id) DO NOTHING
                    """,
                    (
                        o["order_id"], o["user_id"], o["amount"],
                        o["refund_amount"], o["status"],
                        o["created_at"], o["shipped_at"],
                        o["delivered_at"], o["sla_days"],
                    ),
                )

            # reviews
            for r in reviews:
                cur.execute(
                    """
                    INSERT INTO reviews
                        (review_id, user_id, order_id, rating, text, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (review_id) DO NOTHING
                    """,
                    (
                        r["review_id"], r["user_id"], r["order_id"],
                        r["rating"], r["text"], r["created_at"],
                    ),
                )

        conn.commit()

    print(f"Inserted {len(users)} users, {len(orders)} orders, {len(reviews)} reviews.")


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        reset_tables()
    insert_all()