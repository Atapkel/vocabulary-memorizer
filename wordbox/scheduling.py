import time
from .config import LEARNING_STEPS_MIN, LEECH_THRESHOLD

def rate(review, rating):
    """Schedule from the learner's simple Know / Don't know answer."""
    rating = {"know": "good", "dont_know": "again"}.get(rating, rating)
    if rating not in {"again", "good"}:
        raise ValueError("Invalid review answer")
    now = int(time.time())
    r = dict(review)
    if r["state"] in ("new", "learning"):
        if rating == "again":
            r["state"] = "learning"; r["step"] = 0; r["lapses"] += 1; r["direction"] = "recognition"
            r["due"] = now + LEARNING_STEPS_MIN[0] * 60
        elif rating == "good":
            next_step = r["step"] + 1
            if next_step >= len(LEARNING_STEPS_MIN):
                r["state"] = "review"; r["interval"] = 1; r["reps"] = 1
                r["due"] = now + 1 * 86400
            else:
                r["state"] = "learning"; r["step"] = next_step
                r["due"] = now + LEARNING_STEPS_MIN[next_step] * 60
    else:
        qmap = {"again": 2, "good": 4}
        q = qmap[rating]
        if q < 3:
            r["lapses"] += 1; r["reps"] = 0; r["state"] = "learning"; r["step"] = 0; r["direction"] = "recognition"
            r["ef"] = max(1.3, r["ef"] - 0.2)
            r["due"] = now + LEARNING_STEPS_MIN[0] * 60
        else:
            r["reps"] += 1
            if r["reps"] == 1:
                r["interval"] = 1
            elif r["reps"] == 2:
                r["interval"] = 6
            else:
                r["interval"] = round(r["interval"] * r["ef"])
            if r.get("priority") == "high":
                r["interval"] = max(1, round(r["interval"] * 0.7))
            elif r.get("priority") == "low":
                r["interval"] = max(1, round(r["interval"] * 1.25))
            r["ef"] = max(1.3, r["ef"] + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
            r["due"] = now + r["interval"] * 86400
            # Once recognition is established, alternate recall directions.
            if r["reps"] >= 2:
                r["direction"] = "production" if r.get("direction") == "recognition" else "recognition"
    if r["lapses"] >= LEECH_THRESHOLD:
        r["state"] = "suspended"
        r["due"] = None
    return r


