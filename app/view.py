"""Builds the data a case page needs. No web framework code, so it is easy to test."""
from .engine import LABELS, evaluate, ticket_update


def caution_roots(reviews):
    """Roots where the AI review disagreed or could not be read: these always need a human."""
    return {int(root) for root, review in reviews.items() if not review.get("agree", False)}


def case_view(case, message=None, llm_ready=False):
    chains, decisions = evaluate(case.sessions, set(case.rejected), caution_roots(case.reviews))
    by_sid = {s["sid"]: s for s in case.sessions}
    cards = []
    for chain, decision in zip(chains, decisions):
        cards.append({
            "decision": decision, "chain": chain, "label": LABELS[decision["action"]],
            "root": by_sid[chain["root"]],
            "members": [{**by_sid[m["sid"]], "depth": m["depth"]} for m in chain["members"]],
            "review": case.reviews.get(str(chain["root"])),
        })
    return {"case": case, "cards": cards, "sessions": case.sessions, "audit": case.audit,
            "ticket": ticket_update(case.title, decisions, case.audit), "llm_ready": llm_ready, "message": message}
