"""Blocking-chain analysis and the safety policy. Pure Python: no web or database code."""
from collections import defaultdict
from datetime import datetime, timezone

MIN_BLOCK_S = 300          # shortest block worth acting on
MIN_IDLE_S = 300           # root must have been idle at least this long
MAX_ACTIVE_S = 1800        # an actively running root this long needs a DBA
BIG_BLAST = 20             # this many blocked sessions always needs a human
MAX_AUTO_ROLLBACK_S = 600  # a bigger rollback always needs a human
PROTECTED_USERS = {"SYS", "SYSTEM", "DBSNMP"}
PROTECTED_WORDS = ("batch", "settle", "backup", "dbms_")
LABELS = {"WAIT": "keep waiting", "NOTIFY_DBA": "hand over to a DBA", "KILL_RECOMMENDED": "end the idle root session"}
STATES = {"WAIT": "waiting", "NOTIFY_DBA": "needs_dba", "KILL_RECOMMENDED": "pending"}


def now_label():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def entry(who, text):
    return {"at": now_label(), "who": who, "text": text}


def protected_reason(session):
    if session["user"].upper() in PROTECTED_USERS:
        return f"user {session['user']} is protected"
    program = session["program"].lower()
    for word in PROTECTED_WORDS:
        if word in program:
            return f"program '{session['program']}' looks like critical work ('{word}')"
    return None


def _impact(count, max_wait):
    if count >= 10 or max_wait >= 900:
        return "high"
    if count >= 3 or max_wait >= 300:
        return "medium"
    return "low"


def _chain(root, members, by_sid, cycle=False, blocked=None):
    blocked = blocked if blocked is not None else [m["sid"] for m in members]
    max_wait = max((by_sid[s]["wait_s"] for s in blocked), default=0)
    return {"root": root, "cycle": cycle, "members": members, "blocked_count": len(blocked),
            "max_wait_s": max_wait, "depth": max((m["depth"] for m in members), default=0),
            "impact": _impact(len(blocked), max_wait)}


def analyse(sessions):
    """Find every blocking chain: its root blocker, who is stuck behind it, and any circular waits."""
    by_sid = {s["sid"]: s for s in sessions}
    waiting = {s["sid"]: s["blocking_sid"] for s in sessions
               if s["status"] != "KILLED" and s["blocking_sid"] in by_sid}
    children = defaultdict(list)
    for sid, blocker in sorted(waiting.items()):
        children[blocker].append(sid)
    roots = sorted({b for b in waiting.values() if b not in waiting})
    chains, reached = [], set()
    for root in roots:
        members, level = [], [(c, 1) for c in children[root]]
        while level:
            following = []
            for sid, depth in level:
                if sid in reached:
                    continue
                reached.add(sid)
                members.append({"sid": sid, "depth": depth})
                following += [(c, depth + 1) for c in children[sid]]
            level = following
        chains.append(_chain(root, members, by_sid))
    stuck = sorted(sid for sid in waiting if sid not in reached)
    if stuck:
        members = [{"sid": sid, "depth": 1} for sid in stuck[1:]]
        chains.append(_chain(stuck[0], members, by_sid, cycle=True, blocked=stuck))
    return chains


def decide(chain, by_sid, caution=False):
    root = by_sid[chain["root"]]
    action, approval, rules = "WAIT", False, []
    reason = protected_reason(root)
    if chain["cycle"]:
        action = "NOTIFY_DBA"
        rules.append(("R0", "Circular wait: sessions block each other, so a DBA must resolve it"))
    elif reason:
        action = "NOTIFY_DBA"
        rules.append(("R1", f"Root blocker is protected: {reason}. The tool never acts on it"))
    elif chain["max_wait_s"] < MIN_BLOCK_S:
        rules.append(("R2", f"Longest wait is {chain['max_wait_s']} s, under the {MIN_BLOCK_S} s threshold"))
    elif root["status"] == "ACTIVE":
        if root["txn_s"] < MAX_ACTIVE_S:
            rules.append(("R3", f"Root is actively running (transaction {root['txn_s']} s); let it finish"))
        else:
            action = "NOTIFY_DBA"
            rules.append(("R4", f"Root has run for {root['txn_s']} s, over {MAX_ACTIVE_S} s: a DBA should decide"))
    elif root["idle_s"] < MIN_IDLE_S:
        rules.append(("R5", f"Root has been idle only {root['idle_s']} s, under the {MIN_IDLE_S} s threshold"))
    else:
        action = "KILL_RECOMMENDED"
        rules.append(("R6", f"Root is idle in a transaction for {root['idle_s']} s while blocking "
                            f"{chain['blocked_count']} sessions"))
        if chain["blocked_count"] >= BIG_BLAST:
            approval = True
            rules.append(("R7", f"{chain['blocked_count']} blocked sessions is a big blast radius: a human must approve"))
        if root["rollback_s"] > MAX_AUTO_ROLLBACK_S:
            approval = True
            rules.append(("R8", f"Ending the root would trigger a rollback of about {root['rollback_s']} s "
                                "that keeps locks held: a human must approve"))
        if caution:
            approval = True
            rules.append(("R9", "The AI review raised a concern, so a human must approve"))
    return {"root": chain["root"], "action": action, "requires_approval": approval,
            "auto_executable": action == "KILL_RECOMMENDED" and not approval, "state": STATES[action],
            "rules": [{"id": i, "text": t} for i, t in rules], "blocked_count": chain["blocked_count"],
            "max_wait_s": chain["max_wait_s"], "impact": chain["impact"]}


def evaluate(sessions, rejected=(), caution=()):
    chains = analyse(sessions)
    by_sid = {s["sid"]: s for s in sessions}
    decisions = []
    for chain in chains:
        decision = decide(chain, by_sid, chain["root"] in caution)
        if chain["root"] in rejected:
            decision["state"] = "rejected"
        decisions.append(decision)
    return chains, decisions


def apply_kill(sessions, chain):
    """Simulated: the root is ended and everything queued behind it is released."""
    released = {m["sid"] for m in chain["members"]}
    result = []
    for s in sessions:
        s = dict(s)
        if s["sid"] == chain["root"]:
            s["status"], s["blocking_sid"] = "KILLED", None
        elif s["sid"] in released:
            s["blocking_sid"], s["wait_s"] = None, 0
        result.append(s)
    return result


def advance(sessions, seconds):
    result = []
    for s in sessions:
        s = dict(s)
        if s["status"] != "KILLED":
            s["txn_s"] += seconds
            if s["status"] == "INACTIVE":
                s["idle_s"] += seconds
            if s["blocking_sid"] is not None:
                s["wait_s"] += seconds
        result.append(s)
    return result


def auto_run(sessions, mode, rejected, caution, audit):
    """In auto mode, carry out only the kills the policy allows without a human. Adds to `audit`."""
    if mode != "auto":
        return sessions
    chains, decisions = evaluate(sessions, rejected, caution)
    for chain, decision in zip(chains, decisions):
        if decision["auto_executable"] and decision["state"] == "pending":
            sessions = apply_kill(sessions, chain)
            audit.append(entry("auto", f"Ended root session {chain['root']} under policy "
                                       f"({', '.join(r['id'] for r in decision['rules'])}); "
                                       f"{chain['blocked_count']} blocked sessions released (simulated)"))
    return sessions


def ticket_update(title, decisions, audit):
    lines = [f"Blocking session update (simulated): {title}"]
    if not decisions:
        lines.append("No blocking sessions remain.")
    for d in decisions:
        approval = " (needs approval)" if d["requires_approval"] else ""
        lines.append(f"- Root session {d['root']}: {d['blocked_count']} blocked, longest wait {d['max_wait_s']} s, "
                     f"impact {d['impact']}. Decision: {LABELS[d['action']]}{approval}. State: {d['state']}.")
    if audit:
        lines += ["", "Actions so far:"] + [f"- {a['at']} [{a['who']}] {a['text']}" for a in audit]
    return "\n".join(lines) + "\n"
