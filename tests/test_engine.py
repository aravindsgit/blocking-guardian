from app.engine import (
    BIG_BLAST, PROTECTED_USERS, advance, analyse, apply_kill, auto_run, decide, entry, evaluate, ticket_update,
)
from app.scenarios import SCENARIOS, generate


def summary(key, **kw):
    _, decisions = evaluate(generate(key), **kw)
    return [(d["root"], d["action"], d["requires_approval"]) for d in decisions]


def test_same_seed_gives_same_sessions():
    assert generate("idle_blocker", 3) == generate("idle_blocker", 3)
    assert generate("idle_blocker", 3) != generate("idle_blocker", 4)


def test_chain_analysis_finds_root_and_blocked_sessions():
    chains = analyse(generate("idle_blocker"))
    assert len(chains) == 1 and chains[0]["root"] == 210
    assert chains[0]["blocked_count"] == 4 and not chains[0]["cycle"]


def test_each_scenario_gets_the_designed_decision():
    assert summary("idle_blocker") == [(210, "KILL_RECOMMENDED", False)]
    assert summary("batch_blocker") == [(305, "NOTIFY_DBA", False)]
    assert summary("mass_block") == [(410, "KILL_RECOMMENDED", True)]
    assert summary("short_wait") == [(510, "WAIT", False)]
    assert summary("two_chains") == [(610, "KILL_RECOMMENDED", False), (620, "NOTIFY_DBA", False)]
    assert summary("circular_wait") == [(710, "NOTIFY_DBA", False)]


def test_protected_roots_are_never_recommended_for_a_kill():
    for key in SCENARIOS:
        sessions = generate(key)
        by_sid = {s["sid"]: s for s in sessions}
        chains, decisions = evaluate(sessions)
        for chain, decision in zip(chains, decisions):
            root = by_sid[chain["root"]]
            if root["user"] in PROTECTED_USERS or "batch" in root["program"].lower():
                assert decision["action"] != "KILL_RECOMMENDED", key
                assert not decision["auto_executable"], key


def test_big_blast_radius_needs_approval_and_is_not_auto_executable():
    _, decisions = evaluate(generate("mass_block"))
    d = decisions[0]
    assert d["blocked_count"] >= BIG_BLAST and d["requires_approval"] and not d["auto_executable"]
    assert {"R6", "R7", "R8"} <= {r["id"] for r in d["rules"]}


def test_circular_wait_is_detected_and_handed_to_a_dba():
    chains = analyse(generate("circular_wait"))
    assert len(chains) == 1 and chains[0]["cycle"] and chains[0]["blocked_count"] == 2


def test_time_makes_a_short_wait_escalate():
    assert summary("short_wait")[0][1] == "WAIT"
    sessions = advance(generate("short_wait"), 300)
    _, decisions = evaluate(sessions)
    assert decisions[0]["action"] == "KILL_RECOMMENDED"


def test_ai_caution_forces_a_human_but_never_loosens_anything():
    assert summary("idle_blocker", caution={210}) == [(210, "KILL_RECOMMENDED", True)]
    assert summary("batch_blocker", caution={305}) == [(305, "NOTIFY_DBA", False)]
    assert summary("short_wait", caution={510}) == [(510, "WAIT", False)]


def test_rejected_recommendation_is_marked_rejected():
    _, decisions = evaluate(generate("idle_blocker"), rejected={210})
    assert decisions[0]["state"] == "rejected"


def test_apply_kill_releases_the_queue_and_clears_the_chain():
    sessions = generate("idle_blocker")
    chain = analyse(sessions)[0]
    after = apply_kill(sessions, chain)
    assert next(s for s in after if s["sid"] == 210)["status"] == "KILLED"
    assert analyse(after) == []
    assert len(sessions) == len(after)


def test_auto_mode_runs_only_low_risk_kills():
    audit = []
    after = auto_run(generate("two_chains"), "auto", set(), set(), audit)
    status = {s["sid"]: s["status"] for s in after}
    assert status[610] == "KILLED" and status[620] != "KILLED"
    assert len(audit) == 1 and "610" in audit[0]["text"] and audit[0]["who"] == "auto"
    audit = []
    after = auto_run(generate("mass_block"), "auto", set(), set(), audit)
    assert next(s for s in after if s["sid"] == 410)["status"] != "KILLED" and audit == []
    assert auto_run(generate("idle_blocker"), "approval", set(), set(), []) == generate("idle_blocker")


def test_decide_uses_wait_when_idle_is_too_short():
    sessions = generate("idle_blocker")
    sessions = [dict(s, idle_s=60) if s["sid"] == 210 else s for s in sessions]
    chains = analyse(sessions)
    d = decide(chains[0], {s["sid"]: s for s in sessions})
    assert d["action"] == "WAIT" and d["rules"][0]["id"] == "R5"


def test_ticket_update_lists_decisions_and_actions():
    chains, decisions = evaluate(generate("two_chains"))
    text = ticket_update("Two chains", decisions, [entry("human", "Approved something")])
    assert "Root session 610" in text and "needs approval" not in text
    assert "hand over to a DBA" in text and "[human] Approved something" in text
    assert "No blocking sessions remain." in ticket_update("x", [], [])
