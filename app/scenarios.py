"""Invented database sessions. Nothing here comes from a real system."""
import random

SCENARIOS = {
    "idle_blocker": {"title": "Idle blocker",
                     "about": "An application session left a transaction open and idle, blocking four others."},
    "batch_blocker": {"title": "Batch job blocker",
                      "about": "A long nightly batch job holds locks that six sessions are waiting for."},
    "mass_block": {"title": "Mass blocking",
                   "about": "One idle session with a large open transaction blocks 24 sessions."},
    "short_wait": {"title": "Short wait",
                   "about": "A brief block: too early to act. Advance time to watch the policy escalate."},
    "two_chains": {"title": "Two chains",
                   "about": "Two separate blocking chains: one safe to clear, one owned by a protected user."},
    "circular_wait": {"title": "Circular wait",
                      "about": "Two sessions block each other, so there is no single root blocker."},
}
USERS = ("APP_ORDERS", "APP_PORTAL", "APP_REPORTS")


def _s(sid, user, program, status="ACTIVE", blocking=None, wait=0, idle=0, txn=0, rollback=0,
       obj="", sql=""):
    return {"sid": sid, "user": user, "program": program, "status": status, "blocking_sid": blocking,
            "wait_s": wait, "idle_s": idle, "txn_s": txn, "rollback_s": rollback, "object": obj, "sql": sql}


def _waiters(rng, first, count, blocker, low, high, obj="ORDER_LINES"):
    return [_s(first + i, rng.choice(USERS), "order-svc", blocking=blocker, wait=rng.randint(low, high),
               obj=obj, sql=f"UPDATE {obj.lower()} SET state = :s WHERE id = :id") for i in range(count)]


def generate(scenario, seed=7):
    rng = random.Random(seed)
    sessions = [_s(900 + i, rng.choice(USERS), "order-svc", status=rng.choice(["ACTIVE", "INACTIVE"]),
                   idle=rng.randint(0, 20), txn=rng.randint(0, 30), obj="ORDERS",
                   sql="SELECT * FROM orders WHERE id = :id") for i in range(8)]
    idle_sql = "UPDATE order_lines SET state = 'HELD' WHERE order_id = :id"
    if scenario == "idle_blocker":
        sessions += [_s(210, "APP_ORDERS", "order-svc", "INACTIVE", idle=780, txn=800, rollback=20,
                        obj="ORDER_LINES", sql=idle_sql)] + _waiters(rng, 211, 4, 210, 600, 760)
    elif scenario == "batch_blocker":
        sessions += [_s(305, "BATCH_SVC", "nightly_settlement_batch", txn=2400, rollback=1800,
                        obj="SETTLEMENTS", sql="UPDATE settlements SET closed = 1")]
        sessions += _waiters(rng, 306, 6, 305, 900, 1500, "SETTLEMENTS")
    elif scenario == "mass_block":
        sessions += [_s(410, "APP_ORDERS", "order-svc", "INACTIVE", idle=1200, txn=1500, rollback=900,
                        obj="ORDER_LINES", sql=idle_sql)] + _waiters(rng, 411, 24, 410, 200, 1100)
    elif scenario == "short_wait":
        sessions += [_s(510, "APP_PORTAL", "portal-web", "INACTIVE", idle=90, txn=100, rollback=10,
                        obj="ORDER_LINES", sql=idle_sql)] + _waiters(rng, 511, 2, 510, 40, 80)
    elif scenario == "two_chains":
        sessions += [_s(610, "APP_ORDERS", "order-svc", "INACTIVE", idle=700, txn=720, rollback=15,
                        obj="ORDER_LINES", sql=idle_sql)] + _waiters(rng, 611, 3, 610, 500, 650)
        sessions += [_s(620, "SYS", "sqlplus", txn=400, rollback=40, obj="CUSTOMERS",
                        sql="ALTER TABLE customers MODIFY (note VARCHAR2(400))")]
        sessions += _waiters(rng, 621, 2, 620, 350, 400, "CUSTOMERS")
    elif scenario == "circular_wait":
        sessions += [_s(710, "APP_ORDERS", "order-svc", blocking=711, wait=400, txn=420, obj="ORDER_LINES",
                        sql=idle_sql),
                     _s(711, "APP_PORTAL", "portal-web", blocking=710, wait=410, txn=430, obj="CUSTOMERS",
                        sql="UPDATE customers SET tier = :t WHERE id = :id")]
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    return sessions
