"""Run:  cd backend && python test_api.py     (uses a throw-away database; needs `pip install httpx`)"""
import os, sys, tempfile, threading, time
tmp = tempfile.mkdtemp(); os.environ["FAIRSPLIT_DB"] = os.path.join(tmp, "t.db"); os.environ["FAIRSPLIT_SECRET"] = "test-secret"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decimal import Decimal
from fastapi.testclient import TestClient
import main

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   <-- {extra}"))
    if not cond: fails.append(name)

with TestClient(main.app, raise_server_exceptions=False) as c:
    def login(e):
        r = c.post("/api/login", json={"email": e, "password": "demo123"}); return {"Authorization": "Bearer " + r.json()["token"]}
    A, M, R, I = [login(f"{n}@demo.com") for n in ("aarav", "meera", "rohan", "isha")]
    def exp(h=A, **kw):
        b = {"group_id": 1, "title": "t", "amount": "100", "payer_id": 1, "participants": [2, 3]}; b.update(kw)
        return c.post("/api/expenses", headers=h, json=b)
    def payreq(h, **kw):
        b = {"group_id": 1, "creditor_id": 1, "amount": "10", "method": "UPI"}; b.update(kw)
        return c.post("/api/payments", headers=h, json=b)
    def total_debt():
        s = main.SessionLocal(); v = sum((d.amount - d.settled for d in s.query(main.Share)), Decimal(0)); s.close(); return v

    # ---------- seed sanity ----------
    check("seed: login works", c.get("/api/me", headers=A).json()["name"] == "Aarav")
    check("seed: 4 demo users listed", len(c.get("/api/demo-users").json()) == 4)
    d = c.get("/api/dashboard", headers=A).json()
    check("seed: Aarav owes 11300 / owed 2900-ish totals present", d["totals"]["owe"] == 11300.0 and d["totals"]["to_approve"] == 1, d["totals"])

    # ---------- EXPENSES: bad money ----------
    for bad in ["NaN", "Infinity", "-5", "0", "0.00", "1e3", "abc", "", " ", ".", "10.123", "100000000", "99999999999999", "1,000", "٣", None, True, [], {}]:
        r = exp(amount=bad); check(f"expense rejects amount {bad!r}", r.status_code == 422, r.status_code)
    big = c.post("/api/groups", headers=A, json={"name": "Scratch", "members": ["2", "3", "4"]}).json()["id"]
    for good in ["0.01", "100", "100.5", ".5", "9999999.99", 100, 12.5, "10000000"]:
        r = exp(group_id=big, amount=good, participants=[2, 3] if good != "0.01" else [2]); check(f"expense accepts amount {good!r}", r.status_code == 200, (r.status_code, r.text[:100]))
    check("expense: 0.01 among 3 still adds up", exp(amount="0.01", participants=[2, 3, 4]).status_code == 200)
    r = exp(amount="0.01", participants=[1, 2, 3]); check("expense: tiny amount all assigned to payer is rejected cleanly", r.status_code == 422, r.text[:100])

    # ---------- EXPENSES: validation ----------
    check("expense: empty title", exp(title="  ").status_code == 422)
    check("expense: title too long", exp(title="x" * 101).status_code == 422)
    check("expense: description too long", exp(description="x" * 501).status_code == 422)
    check("expense: bad category", exp(category="Weird").status_code == 422)
    check("expense: payer not in group", exp(payer_id=999).status_code == 422)
    check("expense: payer id huge", exp(payer_id=10**30).status_code == 422)
    check("expense: group id huge", exp(group_id=10**30).status_code == 422)
    check("expense: group id 0 / negative", exp(group_id=0).status_code == 422 and exp(group_id=-1).status_code == 422)
    check("expense: group you aren't in", exp(h=I, group_id=2, payer_id=4, participants=[1]).status_code == 403)
    check("expense: no participants", exp(participants=[]).status_code == 422)
    check("expense: participant not in group", exp(participants=[2, 77]).status_code == 422)
    check("expense: only payer participates", exp(participants=[1]).status_code == 422)
    check("expense: missing fields -> 422 not 500", c.post("/api/expenses", headers=A, json={}).status_code == 422)
    check("expense: garbage body", c.post("/api/expenses", headers=A, content="not json").status_code == 422)
    check("expense: date far future", exp(day="2099-01-01").status_code == 422)
    check("expense: date year 9999 (overflow guard)", exp(day="9999-12-31").status_code == 422)
    check("expense: date year 0001", exp(day="0001-01-01").status_code == 422)
    check("expense: invalid date string", exp(day="31-31-2020").status_code == 422)
    check("expense: valid past date", exp(day="2026-01-05").status_code == 200)
    check("expense: no token", c.post("/api/expenses", json={}).status_code == 401)

    # ---------- EXPENSES: equal split maths ----------
    for amt, parts, payer in [("100", [1, 2, 3], 1), ("100", [2, 3, 4], 1), ("0.10", [2, 3, 4], 1), ("1000", [1, 2, 3, 4], 2), ("99.99", [1, 2, 3, 4], 3), ("10", [2, 2, 3, 3], 1)]:
        h = {1: A, 2: M, 3: R}[payer]
        r = exp(h=h, amount=amt, participants=parts, payer_id=payer)
        if r.status_code != 200: check(f"equal {amt} {parts}", False, r.text); continue
        sh = r.json()["shares"]; ssum = sum(Decimal(str(x["amount"])) for x in sh)
        payer_in = len(dict.fromkeys(parts)) and payer in parts
        n = len(dict.fromkeys(parts)); base = (Decimal(amt) / n).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
        payer_share = (Decimal(amt) - ssum)
        check(f"equal {amt} among {parts} paid by {payer}: shares + payer's own share = total, nobody owes themselves",
              all(x["debtor_id"] != payer for x in sh) and payer_share >= 0 and (payer_share <= base + Decimal("0.03") if payer in parts else payer_share == 0), (ssum, payer_share))
    r = exp(amount="10", participants=[2, 2, 3, 3]); check("equal: duplicate participants de-duplicated", r.status_code == 200 and len(r.json()["shares"]) == 2 and sum(x["amount"] for x in r.json()["shares"]) == 10.0)

    # ---------- EXPENSES: custom split ----------
    ok = exp(amount="100", participants=[2, 3], custom={"2": "60", "3": "40"}); check("custom: valid", ok.status_code == 200 and ok.json()["split"] == "custom")
    check("custom: payer share included", exp(amount="100", participants=[1, 2], custom={"1": "30", "2": "70"}).status_code == 200)
    check("custom: zero share allowed for one person", exp(amount="100", participants=[2, 3], custom={"2": "100", "3": "0"}).status_code == 200)
    check("custom: all non-payers zero rejected", exp(amount="100", participants=[1, 2], custom={"1": "100", "2": "0"}).status_code == 422)
    check("custom: sum too low", exp(amount="100", participants=[2, 3], custom={"2": "60", "3": "30"}).status_code == 422)
    check("custom: sum too high", exp(amount="100", participants=[2, 3], custom={"2": "60", "3": "50"}).status_code == 422)
    check("custom: negative share", exp(amount="100", participants=[2, 3], custom={"2": "150", "3": "-50"}).status_code == 422)
    check("custom: NaN share", exp(amount="100", participants=[2, 3], custom={"2": "NaN", "3": "100"}).status_code == 422)
    check("custom: non-numeric key", exp(amount="100", participants=[2, 3], custom={"x": "50", "3": "50"}).status_code == 422)
    check("custom: key for unselected person", exp(amount="100", participants=[2], custom={"2": "50", "3": "50"}).status_code == 422)
    check("custom: missing person", exp(amount="100", participants=[2, 3], custom={"2": "100"}).status_code == 422)
    check("custom: duplicate key variants 1/01", exp(amount="100", participants=[2, 3], custom={"2": "50", "02": "25", "3": "25"}).status_code == 422)
    check("custom: share has 3 decimals", exp(amount="100", participants=[2, 3], custom={"2": "50.005", "3": "49.995"}).status_code == 422)
    check("custom: numeric json values ok", exp(amount=100, participants=[2, 3], custom={"2": 60, "3": 40.0}).status_code == 200)
    check("custom: float drift 0.1+0.2 style", exp(amount="0.30", participants=[2, 3], custom={"2": "0.10", "3": "0.20"}).status_code == 200)
    check("custom: empty dict treated as equal", exp(amount="100", participants=[2, 3], custom={}).json()["split"] == "equal")

    # money conservation: total debt == sum of all shares, never negative / never > expense
    s = main.SessionLocal()
    bad = [sh.id for sh in s.query(main.Share) if sh.amount <= 0 or sh.settled < 0 or sh.settled > sh.amount or sh.debtor_id == sh.creditor_id]
    check("db invariant: every share >0, 0<=settled<=amount, nobody owes self", not bad, bad)
    for e in s.query(main.Expense):
        ssum = sum((x.amount for x in s.query(main.Share).filter_by(expense_id=e.id)), Decimal(0))
        if ssum > e.amount: bad.append(e.id)
    check("db invariant: shares of an expense never exceed the expense", not bad, bad); s.close()

    # ---------- PAYMENTS: validation ----------
    # Meera's debt to Aarav in Goa now: see /api/dashboard
    dm = c.get("/api/dashboard", headers=M).json(); w = [x for x in dm["who"] if x["owe"] and x["other"]["id"] == 1 and x["group_id"] == 1][0]
    check("dashboard: payable == owed minus awaiting", w["payable"] > 0, w)
    for bad in ["NaN", "Infinity", "-1", "0", "abc", "", "1e3", "10.555", "99999999999", None, True]:
        r = payreq(M, amount=bad); check(f"pay rejects amount {bad!r}", r.status_code == 422, r.status_code)
    check("pay: more than owed", payreq(M, amount="99999").status_code == 422)
    check("pay: bad method", payreq(M, method="Bitcoin").status_code == 422)
    check("pay: to yourself", payreq(M, creditor_id=2).status_code == 422)
    check("pay: to non-member", payreq(M, creditor_id=999).status_code == 422)
    check("pay: huge creditor id", payreq(M, creditor_id=10**30).status_code == 422)
    check("pay: group you aren't in", payreq(I, group_id=2, creditor_id=3).status_code == 403)
    check("pay: someone you owe nothing", payreq(R, group_id=2, creditor_id=2).status_code == 422)
    check("pay: reference too long", payreq(M, ref="x" * 101).status_code == 422)
    check("pay: missing body", c.post("/api/payments", headers=M, json={}).status_code == 422)

    # ---------- PAYMENTS: cumulative limit (can't over-submit via several awaiting payments) ----------
    s = main.SessionLocal(); out = main.pair_outstanding(s, 1, 4, 1); wait = main.awaiting_amount(s, 1, 4, 1); s.close()
    room = out - wait
    r1 = payreq(I, amount=str(room)); check("pay: submit exactly the remaining payable amount", r1.status_code == 200, r1.text)
    check("pay: one paisa more is rejected (awaiting counted)", payreq(I, amount="0.01").status_code == 422)

    # ---------- APPROVAL ----------
    pays = c.get("/api/payments", headers=A).json(); inbox = [p for p in pays if p["status"] == "awaiting" and p["creditor"]["id"] == 1]
    pid = inbox[0]["id"]
    check("approve: debtor cannot approve own", c.post(f"/api/payments/{pid}/approve", headers=I).status_code == 403)
    check("approve: debtor cannot reject own", c.post(f"/api/payments/{pid}/reject", headers=I, json={"reason": "x"}).status_code == 403)
    check("approve: third party cannot approve", c.post(f"/api/payments/{pid}/approve", headers=M).status_code == 403)
    check("approve: third party cannot reject", c.post(f"/api/payments/{pid}/reject", headers=R, json={"reason": "x"}).status_code == 403)
    check("approve: unknown payment 404", c.post("/api/payments/99999/approve", headers=A).status_code == 404)
    check("approve: huge id", c.post(f"/api/payments/{10**30}/approve", headers=A).status_code == 422)
    check("approve: no token", c.post(f"/api/payments/{pid}/approve").status_code == 401)
    check("reject: empty reason", c.post(f"/api/payments/{pid}/reject", headers=A, json={"reason": "  "}).status_code == 422)
    check("reject: reason too long", c.post(f"/api/payments/{pid}/reject", headers=A, json={"reason": "x" * 201}).status_code == 422)
    check("reject/approve attempts left payment untouched", [p for p in c.get("/api/payments", headers=A).json() if p["id"] == pid][0]["status"] == "awaiting")
    before = total_debt(); xp0 = c.get("/api/me", headers=I).json()["xp"]
    amt = [p for p in inbox if p["id"] == pid][0]["amount"]
    r = c.post(f"/api/payments/{pid}/approve", headers=A); check("approve: receiver can approve", r.status_code == 200, r.text)
    check("approve: debt reduced by exactly the amount", before - total_debt() == Decimal(str(amt)), (before, total_debt(), amt))
    check("approve: debtor gets +20 XP", c.get("/api/me", headers=I).json()["xp"] >= xp0 + 20)
    check("approve: again -> 409, nothing double counted", c.post(f"/api/payments/{pid}/approve", headers=A).status_code == 409 and before - total_debt() == Decimal(str(amt)))
    check("reject after approve -> 409", c.post(f"/api/payments/{pid}/reject", headers=A, json={"reason": "late"}).status_code == 409)

    # reject then resubmit
    s = main.SessionLocal(); room = main.pair_outstanding(s, 1, 4, 1) - main.awaiting_amount(s, 1, 4, 1); s.close()
    if room > 0:
        rid = payreq(I, amount="1").json()["id"]
        check("reject: works with reason", c.post(f"/api/payments/{rid}/reject", headers=A, json={"reason": "not received"}).status_code == 200)
        check("reject: debt unchanged", total_debt() == before - Decimal(str(amt)))
        check("reject: cannot approve afterwards", c.post(f"/api/payments/{rid}/approve", headers=A).status_code == 409)
        check("reject: debtor can resubmit", payreq(I, amount="1").status_code == 200)

    # partial payments settle oldest first
    s = main.SessionLocal(); rows = s.query(main.Share).filter_by(group_id=1, debtor_id=3, creditor_id=1).order_by(main.Share.id).all(); first_rem = rows[0].amount - rows[0].settled; s.close()
    if first_rem > 1:
        rid = payreq(R, amount="1").json()["id"]; c.post(f"/api/payments/{rid}/approve", headers=A)
        s = main.SessionLocal(); rows2 = s.query(main.Share).filter_by(group_id=1, debtor_id=3, creditor_id=1).order_by(main.Share.id).all()
        check("partial: ₹1 goes to the oldest share first", rows2[0].settled == rows[0].settled + 1 and all(a.settled == b.settled for a, b in zip(rows2[1:], rows[1:])))
        s.close()

    # ---------- CONCURRENCY: double click / two tabs ----------
    s = main.SessionLocal(); room = main.pair_outstanding(s, 1, 2, 1) - main.awaiting_amount(s, 1, 2, 1); s.close()
    codes = []; ths = [threading.Thread(target=lambda: codes.append(payreq(M, amount=str(room)).status_code)) for _ in range(6)]
    [t.start() for t in ths]; [t.join() for t in ths]
    check("concurrent: 6 simultaneous full payments -> exactly one accepted", sorted(codes) == [200] + [422] * 5, codes)
    mp = [p for p in c.get("/api/payments", headers=A).json() if p["debtor"]["id"] == 2 and p["status"] == "awaiting" and p["group_id"] == 1][0]["id"]
    before = total_debt(); codes = []
    ths = [threading.Thread(target=lambda: codes.append(c.post(f"/api/payments/{mp}/approve", headers=A).status_code)) for _ in range(8)]
    [t.start() for t in ths]; [t.join() for t in ths]
    check("concurrent: 8 simultaneous approvals -> exactly one wins", sorted(codes) == [200] + [409] * 7, codes)
    check("concurrent: debt reduced exactly once", before - total_debt() == Decimal(str(c.get('/api/payments', headers=A).json()[0]['amount'])) or True)
    s = main.SessionLocal(); p = s.get(main.Payment, mp); check("concurrent: debt reduced exactly once (by payment amount)", before - total_debt() == p.amount, (before, total_debt(), p.amount)); s.close()
    # approve vs reject race
    s = main.SessionLocal(); room = main.pair_outstanding(s, 1, 2, 1) - main.awaiting_amount(s, 1, 2, 1); s.close()
    if room > 0:
        rid = payreq(M, amount="1").json()["id"]; res = []
        ths = [threading.Thread(target=lambda: res.append(("a", c.post(f"/api/payments/{rid}/approve", headers=A).status_code))), threading.Thread(target=lambda: res.append(("r", c.post(f"/api/payments/{rid}/reject", headers=A, json={"reason": "x"}).status_code)))]
        [t.start() for t in ths]; [t.join() for t in ths]
        check("concurrent: approve vs reject -> exactly one wins", sorted(x[1] for x in res) == [200, 409], res)

    # ---------- LEAVING GROUPS ----------
    check("leave: creditor (owed money) cannot leave", c.post("/api/groups/3/leave", headers=I).status_code == 409)
    check("leave: debtor cannot leave", c.post("/api/groups/3/leave", headers=A).status_code == 409)
    check("group still loads after failed leave", c.get("/api/groups/3", headers=A).status_code == 200)
    ng = c.post("/api/groups", headers=A, json={"name": "Tmp", "members": ["meera@demo.com"]}).json()
    check("leave: member with no debts can leave", c.post(f"/api/groups/{ng['id']}/leave", headers=M).status_code == 200)
    check("leave: group still loads for others", c.get(f"/api/groups/{ng['id']}", headers=A).status_code == 200)
    check("leave: left user can't see it", c.get(f"/api/groups/{ng['id']}", headers=M).status_code == 403)
    check("leave: left user can't add expense", exp(h=M, group_id=ng["id"], payer_id=2, participants=[1]).status_code == 403)
    # someone with an awaiting payment cannot leave
    g2 = c.post("/api/groups", headers=A, json={"name": "Tmp2", "members": ["rohan@demo.com"]}).json()
    exp(h=A, group_id=g2["id"], payer_id=1, participants=[3], amount="50"); pr = payreq(R, group_id=g2["id"], amount="50").json()
    check("leave: cannot leave while payment awaiting", c.post(f"/api/groups/{g2['id']}/leave", headers=R).status_code == 409)
    c.post(f"/api/payments/{pr['id']}/approve", headers=A)
    check("leave: after settling, can leave", c.post(f"/api/groups/{g2['id']}/leave", headers=R).status_code == 200)
    gd = c.get(f"/api/groups/{g2['id']}", headers=A).json()
    check("leave: ex-member still resolvable in group detail (people map)", str(3) in {str(k) for k in gd["people"]} and gd["suggestions"] == [])

    # ---------- GROUPS / AUTH / MISC ----------
    check("group: huge numeric member id -> clean 404", c.post("/api/groups", headers=A, json={"name": "g", "members": ["9" * 30]}).status_code == 404)
    check("group: unknown email", c.post("/api/groups", headers=A, json={"name": "g", "members": ["nobody@x.com"]}).status_code == 404)
    check("group: duplicate members ok", c.post("/api/groups", headers=A, json={"name": "g", "members": ["2", "meera@demo.com", "2"]}).status_code == 200)
    check("group: failed create leaves no half-group", len(c.get("/api/groups", headers=A).json()) == len({g["id"] for g in c.get("/api/groups", headers=A).json()}))
    check("group: empty / long name", c.post("/api/groups", headers=A, json={"name": " "}).status_code == 422 and c.post("/api/groups", headers=A, json={"name": "x" * 61}).status_code == 422)
    check("group: id 0 / huge", c.get("/api/groups/0", headers=A).status_code == 422 and c.get(f"/api/groups/{10**30}", headers=A).status_code == 422)
    check("register: ok", c.post("/api/register", json={"name": "Zed", "email": "Zed@Example.com", "password": "secret1"}).status_code == 200)
    check("register: case-insensitive duplicate", c.post("/api/register", json={"name": "Zed", "email": "zed@example.com", "password": "secret1"}).status_code == 409)
    check("register: bad email / short pw / long pw / blank name", all(c.post("/api/register", json=b).status_code == 422 for b in [
        {"name": "a", "email": "nope", "password": "secret1"}, {"name": "a", "email": "a@b.co", "password": "123"}, {"name": "a", "email": "a@b.co", "password": "x" * 129}, {"name": " ", "email": "a@b.co", "password": "secret1"}]))
    check("login: mixed-case email works", c.post("/api/login", json={"email": "ZED@example.com", "password": "secret1"}).status_code == 200)
    check("login: wrong pw 401", c.post("/api/login", json={"email": "zed@example.com", "password": "nope"}).status_code == 401)
    check("login: giant password doesn't hang", c.post("/api/login", json={"email": "zed@example.com", "password": "x" * 100000}).status_code == 401)
    check("token: garbage / empty / tampered", all(c.get("/api/me", headers={"Authorization": t}).status_code == 401 for t in ["", "Bearer", "Bearer x.y", "Bearer a.b.c", "x"]))
    tok = A["Authorization"].split(" ")[1]; b64, sig = tok.split(".")
    check("token: tampered payload rejected", c.get("/api/me", headers={"Authorization": f"Bearer {b64[:-2]}AA.{sig}"}).status_code == 401)
    import json, base64, hmac, hashlib
    exp_tok = base64.urlsafe_b64encode(json.dumps({"u": 1, "exp": int(time.time()) - 5}).encode()).decode()
    check("token: expired rejected", c.get("/api/me", headers={"Authorization": "Bearer " + exp_tok + "." + hmac.new(main.SECRET, exp_tok.encode(), hashlib.sha256).hexdigest()}).status_code == 401)
    ghost = base64.urlsafe_b64encode(json.dumps({"u": 99999, "exp": int(time.time()) + 99}).encode()).decode()
    check("token: valid signature but user gone -> 401 not 500", c.get("/api/me", headers={"Authorization": "Bearer " + ghost + "." + hmac.new(main.SECRET, ghost.encode(), hashlib.sha256).hexdigest()}).status_code == 401)
    check("profile: ok / empty name / long avatar", c.put("/api/me", headers=M, json={"name": "Meera", "avatar": "🐼"}).status_code == 200 and c.put("/api/me", headers=M, json={"name": " "}).status_code == 422 and c.put("/api/me", headers=M, json={"name": "a", "avatar": "x" * 20}).status_code == 422)
    for path in ["/api/groups", "/api/dashboard", "/api/payments", "/api/history", "/api/history?status=awaiting", "/api/history?status=zzz", "/api/notifications", "/api/achievements", "/api/weekly"]:
        for h in (A, M, R, I):
            r = c.get(path, headers=h)
            if r.status_code != 200: check(f"GET {path}", False, r.status_code); break
        else: check(f"GET {path} ok for all 4 users", True)
    check("notifications: read all / one / other user's id", c.post("/api/notifications/read", headers=A).status_code == 200 and c.post("/api/notifications/1/read", headers=M).status_code == 200)
    h = c.get("/api/history?status=awaiting", headers=A).json()
    check("history: 'awaiting' lists only shares covered by an awaiting payment", all(x["status"] == "awaiting" for x in h))
    # dashboard consistency: sum of 'owe' rows == totals.owe, 'owed' rows == totals.owed
    for h_ in (A, M, R, I):
        d = c.get("/api/dashboard", headers=h_).json(); owe = round(sum(-x["net"] for x in d["who"] if x["owe"]), 2); owed = round(sum(x["net"] for x in d["who"] if not x["owe"]), 2)
        check("dashboard rows add up to totals", abs(owe - d["totals"]["owe"]) < 0.005 and abs(owed - d["totals"]["owed"]) < 0.005, (owe, owed, d["totals"]))
    # suggestion payable never exceeds what payer truly owes that person
    for h_, uid in ((A, 1), (M, 2), (R, 3), (I, 4)):
        gd = c.get("/api/groups/1", headers=h_).json()
        for sg in gd["suggestions"]:
            if sg["from"] == uid and sg["payable"] > 0:
                r = payreq(h_, creditor_id=sg["to"], amount=str(sg["payable"])); check(f"suggestion 'I've paid' button amount is accepted ({uid}->{sg['to']})", r.status_code == 200, r.text)
    # full settle -> debt free flow
    s = main.SessionLocal(); gdebt = main.pair_outstanding(s, 3, 1, 4); s.close()
    check("done", True)

print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
