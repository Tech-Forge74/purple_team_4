import hmac, hashlib, os, re, base64, json, threading, time, secrets
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, Header, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictInt, StrictFloat
from sqlalchemy import create_engine, Column, Integer, String, Numeric, DateTime, Date, ForeignKey, Text, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, declarative_base, Session

HERE = os.path.dirname(os.path.abspath(__file__))

def load_secret():
    """FAIRSPLIT_SECRET if set, otherwise a random secret generated once and kept in backend/.secret."""
    env = os.environ.get("FAIRSPLIT_SECRET")
    if env: return env.encode()
    path = os.path.join(HERE, ".secret")
    try:
        with open(path, "rb") as fh:
            v = fh.read().strip()
            if v: return v
    except OSError: pass
    v = secrets.token_hex(32).encode()
    try:
        with open(path, "wb") as fh: fh.write(v)
        os.chmod(path, 0o600)
    except OSError: pass
    return v

SECRET = load_secret()
DB_PATH = os.environ.get("FAIRSPLIT_DB", os.path.join(HERE, "fairsplit.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 15})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
LOCK = threading.RLock()          # serialises every money-changing request (SQLite is single-writer anyway)
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("10000000")  # ₹1 crore per expense / payment
MAXID = 2_147_483_647
TOKEN_DAYS = 7
ID = Annotated[int, Field(ge=1, le=MAXID)]
PathID = Annotated[int, Path(ge=1, le=MAXID)]
AMT_RE = re.compile(r"^(?=.)[0-9]{0,8}(\.[0-9]{1,2})?$")
Money = str | StrictInt | StrictFloat   # JSON numbers or strings; booleans are rejected
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def f(x): return float(x or 0)
def money(x): return f"{Decimal(str(x)).quantize(CENT):.2f}"
def parse_amount(v, what="amount", allow_zero=False):
    """Strict money parser: digits with at most 2 decimals, > 0 (unless allowed), <= MAX_AMOUNT. Never raises anything but 422."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)): raise HTTPException(422, f"Enter a valid {what}.")
    t = str(v).strip()
    if not AMT_RE.match(t): raise HTTPException(422, f"Enter a valid {what} (numbers only, up to 2 decimal places).")
    d = Decimal(t).quantize(CENT)
    if d == 0 and not allow_zero: raise HTTPException(422, f"The {what} must be greater than zero.")
    if d > MAX_AMOUNT: raise HTTPException(422, f"The {what} can't be more than ₹{MAX_AMOUNT:,.0f}.")
    return d

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True); name = Column(String); email = Column(String, unique=True)
    pw = Column(String); avatar = Column(String, default="🙂"); xp = Column(Integer, default=0)
    streak = Column(Integer, default=0); last_week = Column(String)
class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True); name = Column(String); created = Column(DateTime, default=now)
    owner_id = Column(Integer, ForeignKey("users.id"))
class Member(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True); group_id = Column(Integer, ForeignKey("groups.id")); user_id = Column(Integer, ForeignKey("users.id"))
class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True); group_id = Column(Integer, ForeignKey("groups.id")); title = Column(String)
    amount = Column(Numeric(12, 2)); payer_id = Column(Integer, ForeignKey("users.id")); category = Column(String)
    day = Column(Date); description = Column(Text, default=""); split = Column(String, default="equal"); created = Column(DateTime, default=now)
class Share(Base):  # expense participant + debt record
    __tablename__ = "debts"
    id = Column(Integer, primary_key=True); expense_id = Column(Integer, ForeignKey("expenses.id")); group_id = Column(Integer, ForeignKey("groups.id"))
    debtor_id = Column(Integer, ForeignKey("users.id")); creditor_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Numeric(12, 2)); settled = Column(Numeric(12, 2), default=0)
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True); group_id = Column(Integer, ForeignKey("groups.id"))
    debtor_id = Column(Integer, ForeignKey("users.id")); creditor_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Numeric(12, 2)); method = Column(String); ref = Column(String, default="")
    status = Column(String, default="awaiting"); reason = Column(String, default="")
    created = Column(DateTime, default=now); decided = Column(DateTime)
class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id")); kind = Column(String)
    text = Column(String); group_id = Column(Integer); read = Column(Integer, default=0); created = Column(DateTime, default=now)
class Achievement(Base):
    __tablename__ = "achievements"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id")); code = Column(String); at = Column(DateTime, default=now)

BADGES = {"first": ("First Settlement", "🦁"), "five": ("Five Payments Completed", "🖐️"), "debtfree": ("Debt-Free Hero", "💸"),
          "weekend": ("Weekend Warrior", "🏖️"), "consistent": ("Consistent Settler", "🔁"), "champion": ("Group Champion", "🏆")}

# ---------- auth ----------
PBKDF2_ITERS = 600_000
def _pbkdf2(p, salt, iters): return hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), iters).hex()
def hash_pw(p, salt=None):
    salt = salt or os.urandom(8).hex()
    return f"{PBKDF2_ITERS}${salt}${_pbkdf2(p, salt, PBKDF2_ITERS)}"
def check_pw(p, stored):
    try:
        parts = stored.split("$")
        iters, salt, digest = (int(parts[0]), parts[1], parts[2]) if len(parts) == 3 else (60_000, parts[0], parts[1])  # 2-part = legacy hash
        return hmac.compare_digest(_pbkdf2(p, salt, iters), digest)
    except Exception: return False
def make_token(uid):
    b = base64.urlsafe_b64encode(json.dumps({"u": uid, "exp": int(time.time()) + TOKEN_DAYS * 86400}).encode()).decode()
    return b + "." + hmac.new(SECRET, b.encode(), hashlib.sha256).hexdigest()

def db():
    s = SessionLocal()
    try: yield s
    finally: s.close()
def me(authorization: str = Header(None), s: Session = Depends(db)):
    bad = HTTPException(401, "Please sign in again.")
    try:
        b, sig = (authorization or "").removeprefix("Bearer ").split(".")
        if not hmac.compare_digest(sig, hmac.new(SECRET, b.encode(), hashlib.sha256).hexdigest()): raise bad
        data = json.loads(base64.urlsafe_b64decode(b))
        if data["exp"] < time.time(): raise bad
        u = s.get(User, int(data["u"]))
    except HTTPException: raise
    except Exception: raise bad
    if not u: raise bad
    return u
def notify(s, uid, kind, text, gid=None): s.add(Notification(user_id=uid, kind=kind, text=text, group_id=gid))
def member_ids(s, gid): return [m.user_id for m in s.query(Member).filter_by(group_id=gid)]
def need_group(s, u, gid):
    if not s.query(Member).filter_by(group_id=gid, user_id=u.id).first(): raise HTTPException(403, "You are not in this group.")
    return s.get(Group, gid)
def pub(u): return {"id": u.id, "name": u.name, "email": u.email, "avatar": u.avatar, "xp": u.xp, "streak": u.streak}

# ---------- balances ----------
ZERO = Decimal(0)
def pair_outstanding(s, gid, debtor, creditor):
    return sum((d.amount - d.settled for d in s.query(Share).filter_by(group_id=gid, debtor_id=debtor, creditor_id=creditor)), ZERO)
def awaiting_amount(s, gid, debtor, creditor):
    return sum((p.amount for p in s.query(Payment).filter_by(group_id=gid, debtor_id=debtor, creditor_id=creditor, status="awaiting")), ZERO)
def user_totals(s, uid, gid=None):
    q = s.query(Share)
    if gid: q = q.filter_by(group_id=gid)
    owe = paid = total = owed = ZERO
    for d in q:
        if d.debtor_id == uid: total += d.amount; paid += d.settled; owe += d.amount - d.settled
        if d.creditor_id == uid: owed += d.amount - d.settled
    pct = 100 if owe == 0 else int(paid / total * 100)
    return {"owe": f(owe), "owed": f(owed), "settled": f(paid), "total": f(total), "pct": pct, "debt_free": owe == 0}
def net_balances(s, gid):
    n = {i: ZERO for i in member_ids(s, gid)}
    for d in s.query(Share).filter_by(group_id=gid):
        r = d.amount - d.settled
        n[d.creditor_id] = n.get(d.creditor_id, ZERO) + r; n[d.debtor_id] = n.get(d.debtor_id, ZERO) - r
    return n
def simplify(s, gid):
    n = net_balances(s, gid)
    cred = sorted([[u, a] for u, a in n.items() if a > 0], key=lambda x: -x[1])
    debt = sorted([[u, -a] for u, a in n.items() if a < 0], key=lambda x: -x[1])
    out = []
    while cred and debt:
        pay = min(cred[0][1], debt[0][1]); out.append({"from": debt[0][0], "to": cred[0][0], "amount": f(pay), "suggested": True})
        cred[0][1] -= pay; debt[0][1] -= pay
        if cred[0][1] == 0: cred.pop(0)
        if debt[0][1] == 0: debt.pop(0)
    return out
def group_progress(s, gid):
    ds = s.query(Share).filter_by(group_id=gid).all(); tot = sum((d.amount for d in ds), ZERO); st = sum((d.settled for d in ds), ZERO)
    return {"total": f(tot), "settled": f(st), "pct": 100 if tot == 0 else int(st / tot * 100)}

def award(s, uid, code):
    if s.query(Achievement).filter_by(user_id=uid, code=code).first(): return
    s.add(Achievement(user_id=uid, code=code)); s.flush(); notify(s, uid, "achievement", f"Achievement unlocked: {BADGES[code][1]} {BADGES[code][0]}")

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine); s = SessionLocal()
    try:
        if not s.query(User).count():
            from seed import seed; seed(s)
    finally: s.close()
    threading.Thread(target=reminder_loop, daemon=True).start()
    yield

app = FastAPI(title="FairSplit", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_headers=["*"], allow_methods=["*"])

class Reg(BaseModel): name: str; email: str; password: str
class Login(BaseModel): email: str; password: str
@app.post("/api/register")
def register(b: Reg, s: Session = Depends(db)):
    name, email = b.name.strip(), b.email.strip().lower()
    if not name or len(name) > 50: raise HTTPException(422, "Enter a name (up to 50 characters).")
    if not EMAIL_RE.match(email) or len(email) > 120: raise HTTPException(422, "Enter a valid email address.")
    if not 6 <= len(b.password) <= 128: raise HTTPException(422, "Password must be 6 to 128 characters.")
    if s.query(User).filter_by(email=email).first(): raise HTTPException(409, "That email is already registered.")
    u = User(name=name, email=email, pw=hash_pw(b.password)); s.add(u)
    try: s.commit()
    except IntegrityError: s.rollback(); raise HTTPException(409, "That email is already registered.")
    return {"token": make_token(u.id), "user": pub(u)}
@app.post("/api/login")
def login(b: Login, s: Session = Depends(db)):
    u = s.query(User).filter_by(email=b.email.strip().lower()).first()
    if not u or len(b.password) > 128 or not check_pw(b.password, u.pw): raise HTTPException(401, "Wrong email or password.")
    return {"token": make_token(u.id), "user": pub(u)}
@app.get("/api/demo-users")
def demo_users(s: Session = Depends(db)): return [pub(u) | {"password": "demo123"} for u in s.query(User).filter(User.id <= 4, User.email.like("%@demo.com"))]
@app.get("/api/me")
def get_me(u: User = Depends(me)): return pub(u)
class Prof(BaseModel): name: str; avatar: str = "🙂"
@app.put("/api/me")
def edit_me(b: Prof, u: User = Depends(me), s: Session = Depends(db)):
    name, av = b.name.strip(), b.avatar.strip() or "🙂"
    if not name or len(name) > 50: raise HTTPException(422, "Name must be 1 to 50 characters.")
    if len(av) > 8: raise HTTPException(422, "Pick a single emoji avatar.")
    u.name, u.avatar = name, av; s.commit(); return pub(u)

# ---------- groups ----------
def group_card(s, g):
    ex = s.query(Expense).filter_by(group_id=g.id)
    return {"id": g.id, "name": g.name, "created": g.created.date().isoformat(), "members": [pub(s.get(User, i)) for i in member_ids(s, g.id)],
            "total_expenses": f(sum((e.amount for e in ex), ZERO)), "progress": group_progress(s, g.id)}
@app.get("/api/groups")
def groups(u: User = Depends(me), s: Session = Depends(db)):
    return [group_card(s, s.get(Group, m.group_id)) for m in s.query(Member).filter_by(user_id=u.id).order_by(Member.group_id)]
class NewGroup(BaseModel): name: str; members: list[str] = Field(default=[], max_length=30)
@app.post("/api/groups")
def new_group(b: NewGroup, u: User = Depends(me), s: Session = Depends(db)):
    name = b.name.strip()
    if not name or len(name) > 60: raise HTTPException(422, "Give the group a name (up to 60 characters).")
    with LOCK:
        g = Group(name=name, owner_id=u.id); s.add(g); s.flush(); ids = {u.id}
        for key in b.members:
            k = key.strip().lower()
            if not k: continue
            m = s.query(User).filter_by(email=k).first()
            if not m and k.isascii() and k.isdigit() and len(k) <= 9: m = s.get(User, int(k))
            if not m: raise HTTPException(404, f"No registered user found for '{key.strip()[:60]}'.")
            ids.add(m.id)
        for i in sorted(ids):
            s.add(Member(group_id=g.id, user_id=i))
            if i != u.id: notify(s, i, "group", f"{u.name} added you to {g.name}", g.id)
        s.commit(); return group_card(s, g)
@app.get("/api/groups/{gid}")
def group_detail(gid: PathID, u: User = Depends(me), s: Session = Depends(db)):
    g = need_group(s, u, gid); card = group_card(s, g)
    exps = s.query(Expense).filter_by(group_id=gid).order_by(Expense.day.desc(), Expense.id.desc()).all()
    card["expenses"] = [expense_out(s, e) for e in exps]
    card["balances"] = {i: user_totals(s, i, gid) for i in member_ids(s, gid)}
    sug = simplify(s, gid)
    for x in sug:  # only offer "I've paid" where the payer really owes that person (payments must match real debts)
        x["payable"] = f(max(ZERO, pair_outstanding(s, gid, x["from"], x["to"]) - awaiting_amount(s, gid, x["from"], x["to"]))) if x["from"] == u.id else 0.0
        x["payable"] = min(x["payable"], x["amount"], f(MAX_AMOUNT))
    card["suggestions"] = sug
    ids = set(member_ids(s, gid)) | {e.payer_id for e in exps} | {sh["debtor_id"] for e in card["expenses"] for sh in e["shares"]}
    card["people"] = {i: pub(s.get(User, i)) for i in ids if s.get(User, i)}   # includes people who have since left
    return card
@app.post("/api/groups/{gid}/leave")
def leave(gid: PathID, u: User = Depends(me), s: Session = Depends(db)):
    with LOCK:
        need_group(s, u, gid); t = user_totals(s, u.id, gid)
        if t["owe"] > 0: raise HTTPException(409, "Settle your outstanding dues before leaving.")
        if t["owed"] > 0: raise HTTPException(409, "Others still owe you money in this group. Collect it before leaving.")
        if s.query(Payment).filter(Payment.group_id == gid, Payment.status == "awaiting", or_(Payment.debtor_id == u.id, Payment.creditor_id == u.id)).count():
            raise HTTPException(409, "A payment in this group is still awaiting approval. Resolve it before leaving.")
        s.query(Member).filter_by(group_id=gid, user_id=u.id).delete(); s.commit(); return {"ok": True}

# ---------- expenses (main feature 1) ----------
class NewExpense(BaseModel):
    group_id: ID; title: str; amount: Money; payer_id: ID; category: str = "Others"; day: date | None = None
    description: str = ""; participants: list[ID] = Field(max_length=100); custom: dict[str, Money] | None = None
CATS = ["Food", "Travel", "Rent", "Groceries", "Shopping", "Utilities", "Others"]
def expense_out(s, e):
    return {"id": e.id, "title": e.title, "amount": f(e.amount), "payer_id": e.payer_id, "category": e.category, "day": e.day.isoformat(), "description": e.description, "split": e.split,
            "group_id": e.group_id, "group": s.get(Group, e.group_id).name,
            "shares": [{"debtor_id": d.debtor_id, "amount": f(d.amount), "settled": f(d.settled)} for d in s.query(Share).filter_by(expense_id=e.id).order_by(Share.id)]}
@app.post("/api/expenses")
def add_expense(b: NewExpense, u: User = Depends(me), s: Session = Depends(db)):
    with LOCK:
        g = need_group(s, u, b.group_id); ids = member_ids(s, g.id)
        title, desc = b.title.strip(), b.description.strip()
        if not title: raise HTTPException(422, "Add an expense title.")
        if len(title) > 100: raise HTTPException(422, "Title can be at most 100 characters.")
        if len(desc) > 500: raise HTTPException(422, "Description can be at most 500 characters.")
        total = parse_amount(b.amount)
        if b.category not in CATS: raise HTTPException(422, "Pick a valid category.")
        day = b.day or date.today()
        if day < date.today() - timedelta(days=1826) or day > date.today() + timedelta(days=366): raise HTTPException(422, "Pick a date within the last 5 years and the next year.")
        if b.payer_id not in ids: raise HTTPException(422, "The payer must be a member of this group.")
        if not b.participants: raise HTTPException(422, "Select at least one person to split with.")
        if any(p not in ids for p in b.participants): raise HTTPException(422, "Everyone you split with must be a member of this group.")
        parts = list(dict.fromkeys(b.participants))
        if b.custom:
            shares = {}
            for k, v in b.custom.items():
                try: uid = int(k)
                except (ValueError, TypeError): raise HTTPException(422, "Custom split has an invalid member.")
                if uid not in parts or uid in shares: raise HTTPException(422, "Custom split must match the people you selected.")
                shares[uid] = parse_amount(v, "share", allow_zero=True)
            if set(shares) != set(parts): raise HTTPException(422, "Enter an amount for everyone you selected.")
            if sum(shares.values(), ZERO) != total: raise HTTPException(422, f"Custom amounts must add up to exactly ₹{money(total)}.")
            split = "custom"
        else:
            base = (total / len(parts)).quantize(CENT, rounding=ROUND_DOWN); shares = {p: base for p in parts}
            shares[parts[0]] += total - base * len(parts); split = "equal"   # leftover paise go to the first person, so shares add up exactly
        debts = {p: a for p, a in shares.items() if p != b.payer_id and a > 0}    # payer never owes themselves
        if not debts: raise HTTPException(422, "Someone other than the payer must owe a share. Add another person or a larger amount.")
        e = Expense(group_id=g.id, title=title, amount=total, payer_id=b.payer_id, category=b.category, day=day, description=desc, split=split)
        s.add(e); s.flush(); payer = s.get(User, b.payer_id)
        for p, amt in debts.items():
            s.add(Share(expense_id=e.id, group_id=g.id, debtor_id=p, creditor_id=b.payer_id, amount=amt, settled=0))
            notify(s, p, "owe", f"You owe {payer.name} ₹{money(amt)} for {e.title} in {g.name}. Equal split, equal pain 🍕" if split == "equal" else f"You owe {payer.name} ₹{money(amt)} for {e.title}. Custom split detected 🦞", g.id)
        for i in ids:
            if i != b.payer_id and i not in debts: notify(s, i, "expense", f"New expense in {g.name}: {e.title} (₹{money(total)})", g.id)
        s.commit(); return expense_out(s, e)

def overdue(d, e): return d.amount > d.settled and e.day + timedelta(days=7) < date.today()
@app.get("/api/dashboard")
def dashboard(u: User = Depends(me), s: Session = Depends(db)):
    t = user_totals(s, u.id); who = {}; remind = []
    for d in s.query(Share).filter(or_(Share.debtor_id == u.id, Share.creditor_id == u.id)).order_by(Share.id):
        r = d.amount - d.settled
        if r <= 0: continue
        e = s.get(Expense, d.expense_id); i_owe = d.debtor_id == u.id; other = d.creditor_id if i_owe else d.debtor_id
        x = who.setdefault((other, d.group_id, i_owe), {"other": pub(s.get(User, other)), "group_id": d.group_id, "group": s.get(Group, d.group_id).name,
                                                       "net": ZERO, "overdue": False, "awaiting": 0.0, "payable": 0.0, "owe": i_owe})
        x["net"] += -r if i_owe else r        # one row per person, per group, per direction (what you owe / what they owe you)
        if i_owe:
            late = overdue(d, e); x["overdue"] = x["overdue"] or late
            remind.append({"title": e.title, "amount": f(r), "to": s.get(User, d.creditor_id).name, "due": (e.day + timedelta(days=7)).isoformat(), "overdue": late})
    for x in who.values():
        if x["owe"]:
            aw = awaiting_amount(s, x["group_id"], u.id, x["other"]["id"]); x["awaiting"] = f(aw); x["payable"] = f(min(MAX_AMOUNT, max(ZERO, -x["net"] - aw)))
        x["net"] = f(x["net"])
    t["pending_payments"] = s.query(Payment).filter_by(debtor_id=u.id, status="awaiting").count()
    t["to_approve"] = s.query(Payment).filter_by(creditor_id=u.id, status="awaiting").count()
    return {"totals": t, "who": sorted(who.values(), key=lambda x: (not x["owe"], x["group_id"])), "reminders": sorted(remind, key=lambda r: r["due"])[:5], "recent": history(None, u, s)[:5], "user": pub(u),
            "achievements": [a.code for a in s.query(Achievement).filter_by(user_id=u.id)]}

# ---------- payments & approval (main feature 2) ----------
class Pay(BaseModel): group_id: ID; creditor_id: ID; amount: Money; method: str; ref: str = ""
@app.post("/api/payments")
def pay(b: Pay, u: User = Depends(me), s: Session = Depends(db)):
    with LOCK:
        need_group(s, u, b.group_id)
        if b.creditor_id == u.id or b.creditor_id not in member_ids(s, b.group_id): raise HTTPException(422, "Choose someone in this group you owe.")
        if b.method not in ("UPI", "Cash", "Bank Transfer", "Other"): raise HTTPException(422, "Pick a payment method.")
        ref = b.ref.strip()
        if len(ref) > 100: raise HTTPException(422, "Reference can be at most 100 characters.")
        amt = parse_amount(b.amount)
        out = pair_outstanding(s, b.group_id, u.id, b.creditor_id); waiting = awaiting_amount(s, b.group_id, u.id, b.creditor_id)
        if out <= 0: raise HTTPException(422, "You don't owe this person anything in this group.")
        if out - waiting <= 0: raise HTTPException(422, f"Your pending payments (₹{money(waiting)}) already cover everything you owe them. Wait for their approval.")
        if amt > out - waiting: raise HTTPException(422, f"You can confirm at most ₹{money(out - waiting)} right now (₹{money(out)} owed, ₹{money(waiting)} already awaiting approval).")
        p = Payment(group_id=b.group_id, debtor_id=u.id, creditor_id=b.creditor_id, amount=amt, method=b.method, ref=ref); s.add(p)
        notify(s, b.creditor_id, "approval", f"{u.name} says they paid you ₹{money(amt)} via {b.method}. Please approve or reject. Ghost Baba is waiting 👀", b.group_id)
        s.commit(); return {"id": p.id, "status": p.status}
def pay_out(s, p):
    return {"id": p.id, "group_id": p.group_id, "group": s.get(Group, p.group_id).name, "debtor": pub(s.get(User, p.debtor_id)), "creditor": pub(s.get(User, p.creditor_id)),
            "amount": f(p.amount), "method": p.method, "ref": p.ref, "status": p.status, "reason": p.reason, "created": p.created.isoformat(), "decided": p.decided and p.decided.isoformat()}
@app.get("/api/payments")
def payments(u: User = Depends(me), s: Session = Depends(db)):
    return [pay_out(s, p) for p in s.query(Payment).filter(or_(Payment.debtor_id == u.id, Payment.creditor_id == u.id)).order_by(Payment.id.desc())]
class Decision(BaseModel): reason: str = ""
def check_payment(s, u, pid):
    p = s.get(Payment, pid)
    if not p: raise HTTPException(404, "Payment not found.")
    if p.debtor_id == u.id: raise HTTPException(403, "You can't approve or reject your own payment.")
    if p.creditor_id != u.id: raise HTTPException(403, "Only the receiver can decide this payment.")
    if p.status != "awaiting": raise HTTPException(409, f"This payment was already {p.status}.")
    return p
def claim(s, p, status):
    """Atomically move awaiting -> status. Exactly one request can win, even with several server processes."""
    if s.query(Payment).filter(Payment.id == p.id, Payment.status == "awaiting").update({"status": status}, synchronize_session=False) != 1:
        raise HTTPException(409, "This payment was already decided.")
    s.refresh(p)
@app.post("/api/payments/{pid}/approve")
def approve(pid: PathID, u: User = Depends(me), s: Session = Depends(db)):
    with LOCK:
        p = check_payment(s, u, pid); left = p.amount
        rows = s.query(Share).filter_by(group_id=p.group_id, debtor_id=p.debtor_id, creditor_id=p.creditor_id).order_by(Share.id).all()
        if sum((d.amount - d.settled for d in rows), ZERO) < p.amount: raise HTTPException(409, "Payment exceeds the outstanding debt.")
        claim(s, p, "approved")
        for d in rows:                       # oldest shares first; partial payments supported
            take = min(left, d.amount - d.settled); d.settled += take; left -= take
            if left == 0: break
        p.decided = now(); debtor = s.get(User, p.debtor_id); g = s.get(Group, p.group_id)
        debtor.xp += 20; extra = ""
        wk = date.today().strftime("%G-W%V"); prev = (date.today() - timedelta(days=7)).strftime("%G-W%V")
        if debtor.last_week != wk: debtor.streak = debtor.streak + 1 if debtor.last_week == prev else 1; debtor.last_week = wk
        s.flush(); tot = user_totals(s, debtor.id)
        if tot["debt_free"]: debtor.xp += 10; award(s, debtor.id, "debtfree"); extra = " Debt-free! 💸"
        n = s.query(Payment).filter_by(debtor_id=debtor.id, status="approved").count()
        award(s, debtor.id, "first")
        if n >= 5: award(s, debtor.id, "five")
        if date.today().weekday() >= 5: award(s, debtor.id, "weekend")
        if debtor.streak >= 3: award(s, debtor.id, "consistent")
        if group_progress(s, g.id)["pct"] == 100:
            for i in member_ids(s, g.id): award(s, i, "champion")
        remaining = f(pair_outstanding(s, p.group_id, p.debtor_id, p.creditor_id))
        notify(s, p.debtor_id, "approved", f"{u.name} approved your ₹{money(p.amount)}. ₹{money(remaining)} to go. Tycoon Tiger is proud 🦁 +20 XP.{extra}", g.id)
        notify(s, u.id, "approved", f"You approved ₹{money(p.amount)} from {debtor.name}.", g.id); s.commit()
        return {"ok": True, "debt_free": tot["debt_free"], "remaining": remaining}
@app.post("/api/payments/{pid}/reject")
def reject(pid: PathID, b: Decision, u: User = Depends(me), s: Session = Depends(db)):
    with LOCK:
        p = check_payment(s, u, pid); reason = b.reason.strip()
        if not reason: raise HTTPException(422, "Add a reason so they know what to fix.")
        if len(reason) > 200: raise HTTPException(422, "Reason can be at most 200 characters.")
        claim(s, p, "rejected"); p.reason, p.decided = reason, now()
        notify(s, p.debtor_id, "rejected", f"{u.name} rejected your ₹{money(p.amount)}: {reason}. The debt is still open; you can submit again.", p.group_id); s.commit(); return {"ok": True}

# ---------- history / notifications / rewards / weekly ----------
@app.get("/api/history")
def history(status: str | None = None, u: User = Depends(me), s: Session = Depends(db)):
    mine = or_(Share.debtor_id == u.id, Share.creditor_id == u.id)
    wait = {}
    for p in s.query(Payment).filter(Payment.status == "awaiting", or_(Payment.debtor_id == u.id, Payment.creditor_id == u.id)):
        k = (p.group_id, p.debtor_id, p.creditor_id); wait[k] = wait.get(k, ZERO) + p.amount
    out = []
    for d in s.query(Share).filter(mine).order_by(Share.id):     # oldest first = the order approvals settle in
        e = s.get(Expense, d.expense_id); rem = d.amount - d.settled; st = "approved" if rem <= 0 else "pending"
        k = (d.group_id, d.debtor_id, d.creditor_id)
        if rem > 0 and wait.get(k, ZERO) > 0:
            take = min(wait[k], rem); wait[k] -= take; st = "awaiting"   # only shares actually covered by an awaiting payment
        out.append({"kind": "expense", "title": e.title, "group": s.get(Group, d.group_id).name, "payer": s.get(User, e.payer_id).name, "debtor": s.get(User, d.debtor_id).name,
                    "amount": f(d.amount), "settled": f(d.settled), "status": st, "date": e.day.isoformat()})
    out.reverse()
    for p in s.query(Payment).filter(or_(Payment.debtor_id == u.id, Payment.creditor_id == u.id)).order_by(Payment.id.desc()):
        out.append({"kind": "payment", "title": f"Payment {s.get(User, p.debtor_id).name} → {s.get(User, p.creditor_id).name}", "group": s.get(Group, p.group_id).name,
                    "payer": s.get(User, p.debtor_id).name, "debtor": s.get(User, p.creditor_id).name, "amount": f(p.amount), "settled": f(p.amount) if p.status == "approved" else 0,
                    "status": p.status, "date": p.created.date().isoformat(), "method": p.method, "reason": p.reason, "decided": p.decided and p.decided.isoformat()})
    out.sort(key=lambda x: x["date"], reverse=True)
    return [o for o in out if not status or status == "all" or o["status"] == status]

@app.get("/api/notifications")
def notes(u: User = Depends(me), s: Session = Depends(db)):
    q = s.query(Notification).filter_by(user_id=u.id).order_by(Notification.id.desc()).limit(100)
    return [{"id": n.id, "kind": n.kind, "text": n.text, "group_id": n.group_id, "read": bool(n.read), "created": n.created.isoformat()} for n in q]
@app.post("/api/notifications/read")
def read_all(u: User = Depends(me), s: Session = Depends(db)):
    s.query(Notification).filter_by(user_id=u.id).update({"read": 1}); s.commit(); return {"ok": True}
@app.post("/api/notifications/{nid}/read")
def read_one(nid: PathID, u: User = Depends(me), s: Session = Depends(db)):
    s.query(Notification).filter_by(id=nid, user_id=u.id).update({"read": 1}); s.commit(); return {"ok": True}

@app.get("/api/achievements")
def achievements(u: User = Depends(me), s: Session = Depends(db)):
    got = {a.code: a.at.date().isoformat() for a in s.query(Achievement).filter_by(user_id=u.id)}
    return {"xp": u.xp, "streak": u.streak, "level": u.xp // 100 + 1,
            "badges": [{"code": c, "name": n, "icon": i, "earned": c in got, "at": got.get(c)} for c, (n, i) in BADGES.items()]}
@app.get("/api/weekly")
def weekly(u: User = Depends(me), s: Session = Depends(db)):
    since = now() - timedelta(days=7); out = []
    for m in s.query(Member).filter_by(user_id=u.id).order_by(Member.group_id):
        gid = m.group_id; ex = s.query(Expense).filter(Expense.group_id == gid, Expense.created >= since)
        pays = s.query(Payment).filter(Payment.group_id == gid, Payment.status == "approved", Payment.decided >= since)
        mem = [(i, user_totals(s, i, gid)) for i in member_ids(s, gid)]
        out.append({"group": s.get(Group, gid).name, "expenses_added": f(sum((e.amount for e in ex), ZERO)), "payments_confirmed": f(sum((p.amount for p in pays), ZERO)),
                    "outstanding": f(sum((Decimal(str(t["owe"])) for _, t in mem), ZERO)), "pending": [pub(s.get(User, i)) | {"owe": t["owe"]} for i, t in mem if t["owe"] > 0],
                    "cleared": [pub(s.get(User, i)) for i, t in mem if t["debt_free"]], "progress": group_progress(s, gid)})
    return {"week_ending": date.today().isoformat(), "is_weekend": date.today().weekday() >= 5, "groups": out}

def reminder_loop():  # runs only while the app is running; nothing is "sent" otherwise
    while True:
        s = SessionLocal()
        try:
            if date.today().weekday() >= 5:
                wk = date.today().strftime("%G-W%V")
                with LOCK:
                    for u in s.query(User):
                        t = user_totals(s, u.id)
                        if t["owe"] > 0 and not s.query(Notification).filter(Notification.user_id == u.id, Notification.kind == "weekly", Notification.text.like(f"%({wk})%")).first():
                            notify(s, u.id, "weekly", f"Weekly reminder ({wk}): you still owe ₹{money(t['owe'])}. Ghost Baba spotted near your dues 👀")
                    s.commit()
        except Exception as ex: s.rollback(); print("reminder error", ex)
        finally: s.close()
        time.sleep(3600)
