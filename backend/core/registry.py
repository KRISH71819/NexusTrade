"""Candidate registry pure core (v2 Phase 3). Mongo persistence arrives with the
Phase-4 scheduler; collection name frozen here (_v2 dev suffix rule)."""
import hashlib
import re
from datetime import datetime, timezone

COLLECTION = "alpha_registry_v2"

def normalize_expression(expr: str) -> str:
    # DSL tokens never contain spaces; strip ALL whitespace so cosmetic
    # spacing variants fingerprint identically (duplicate detection).
    return re.sub(r'\s+', '', str(expr).strip().lower())

def expression_fingerprint(expr: str) -> str:
    return hashlib.sha256(normalize_expression(expr).encode()).hexdigest()[:16]

def returns_fingerprint(daily_net) -> str:
    vals = [round(float(v), 6) for v in list(daily_net)[:4000]]
    return hashlib.sha256(",".join(map(str, vals)).encode()).hexdigest()[:16]

def find_duplicate(expr: str, daily_net, known: list) -> str | None:
    """known = list of docs with 'fingerprint' and 'returns_fingerprint'."""
    fp = expression_fingerprint(expr)
    rfp = returns_fingerprint(daily_net)
    for doc in known:
        if doc.get("fingerprint") == fp or doc.get("returns_fingerprint") == rfp:
            return doc.get("name") or doc.get("fingerprint")
    return None

def build_candidate_doc(name, expr, hypothesis, source, metrics, folds, gates,
                        critic, duplicate_of=None, reject_reason=None,
                        returns_fp=None) -> dict:
    dup = bool(duplicate_of)
    final = "PASS_GATES" if (gates.get("all") and not dup and not reject_reason) else "REJECT"
    return {
        "name": name,
        "expression": expr,
        "hypothesis": hypothesis,
        "source": source,
        "created_at": datetime.now(timezone.utc),
        "fingerprint": expression_fingerprint(expr),
        "returns_fingerprint": returns_fp,
        "duplicate_of": duplicate_of,
        "critic": critic,
        "metrics": metrics,
        "fold_sharpes": folds,
        "gates": gates,
        "reject_reason": reject_reason or (critic or {}).get("fatal_flaw") or None,
        "final_verdict": final,
    }
