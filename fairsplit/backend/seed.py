from datetime import date, timedelta
from main import (User, Group, Member, NewExpense, Pay, add_expense, pay, approve, hash_pw)

def seed(s):
    h = hash_pw("demo123")   # one hash reused: key stretching is deliberately slow
    us = [User(name=n, email=e, pw=h, avatar=a) for n, e, a in
          [("Aarav", "aarav@demo.com", "🦊"), ("Meera", "meera@demo.com", "🐼"), ("Rohan", "rohan@demo.com", "🐯"), ("Isha", "isha@demo.com", "🦄")]]
    s.add_all(us); s.flush(); A, M, R, I = [u.id for u in us]
    gids = {}
    for name, ids in [("Goa Trip", [A, M, R, I]), ("Roommates", [A, M, R]), ("Monthly Groceries", [A, I])]:
        g = Group(name=name, owner_id=A); s.add(g); s.flush(); gids[name] = g.id
        for i in ids: s.add(Member(group_id=g.id, user_id=i))
    s.commit()
    goa, room, groc = gids["Goa Trip"], gids["Roommates"], gids["Monthly Groceries"]
    def exp(gid, title, amt, payer, cat, ago, parts, custom=None):
        add_expense(NewExpense(group_id=gid, title=title, amount=amt, payer_id=payer, category=cat, day=date.today() - timedelta(days=ago), participants=parts, custom=custom), s.get(User, payer), s)
    exp(goa, "Beach Villa", "4800", A, "Travel", 9, [A, M, R, I])      # overdue by now
    exp(goa, "Seafood Dinner", "2400", M, "Food", 3, [A, M, R, I], {str(A): "400", str(M): "800", str(R): "600", str(I): "600"})
    exp(goa, "Scooter Rental", "1200", A, "Travel", 2, [A, M, R, I])   # A paid 1200 for 4 => 300 each
    exp(room, "Monthly Rent", "30000", R, "Rent", 5, [A, M, R]); exp(groc, "Weekly Groceries", "1800", I, "Groceries", 1, [A, I])
    def p(gid, d, c, amt): return pay(Pay(group_id=gid, creditor_id=c, amount=amt, method="UPI", ref="UPI123"), s.get(User, d), s)["id"]
    approve(p(goa, M, A, "1200"), s.get(User, A), s)            # Meera's full share to Aarav: approved
    approve(p(goa, R, A, "400"), s.get(User, A), s)             # Rohan's partial payment approved
    p(goa, I, A, "500")                                         # Isha's payment awaits Aarav's approval
    p(room, M, R, "10000")                                      # Meera -> Rohan awaiting
