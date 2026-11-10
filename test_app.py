import os, re, sqlite3, tempfile
TMP = tempfile.mkdtemp()
os.environ["BT_DB"] = os.path.join(TMP, "t.db")

import app as A


def client():
    return A.app.test_client()


def token(c, path="/login"):
    return re.search(r'name="csrf" content="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)


def register(c, name):
    t = token(c, "/register")
    r = c.post("/register", data={"username": name, "password": "correct-horse-1", "confirm": "correct-horse-1", "csrf_token": t})
    assert r.status_code == 302, r.get_data(as_text=True)
    return token(c, "/")


def J(c, t, method, url, body=None):
    return getattr(c, method)(url, json=body, headers={"X-CSRF-Token": t})


# ---------------- first account is always allowed; later sign-ups are closed by default
a = client()
assert b"Create an account" in a.get("/login").data
ta = register(a, "alice")
b = client()
assert b"Create an account" not in b.get("/login").data and b.get("/register").status_code == 404
A.ALLOW_REGISTRATION = True
tb = register(b, "bob")
A.ALLOW_REGISTRATION = False

# ---------------- auth / csrf
assert client().get("/api/bills").status_code == 401
assert client().get("/").status_code == 302
assert client().get("/export.csv").status_code == 302
assert a.post("/api/accounts", json={"name": "x"}).status_code == 400          # no CSRF token
assert a.get("/").status_code == 200 and a.get("/").headers["Content-Security-Policy"].startswith("default-src 'self'")
assert any("bills_session" in h for h in client().get("/login").headers.getlist("Set-Cookie")), "cookie name"

# ---------------- accounts
cap = J(a, ta, "post", "/api/accounts", {"name": "Capital One"}).get_json()["id"]
dug = J(a, ta, "post", "/api/accounts", {"name": "DuGood"}).get_json()["id"]
assert J(a, ta, "post", "/api/accounts", {"name": "capital one"}).status_code == 400          # duplicate (case-insensitive)
assert J(a, ta, "post", "/api/accounts", {"name": "  "}).status_code == 400
assert [x["name"] for x in J(a, ta, "get", "/api/accounts").get_json()] == ["Capital One", "DuGood"]
assert J(b, tb, "get", "/api/accounts").get_json() == []                                      # Bob sees none of Alice's

# ---------------- bills: create / validate
def bill(**kw):
    d = {"name": "Youtube Premium", "account_id": cap, "amount_due": "29.22", "due_date": "2026-11-19",
         "freq_value": 1, "freq_unit": "month"}
    d.update(kw)
    return d

r = J(a, ta, "post", "/api/bills", bill()).get_json()
yt = r["id"]
assert r["amount_due"] == "29.22" and r["next_due"] == "2026-12-19" and r["account"] == "Capital One" and r["balance"] is None
for bad in (bill(name=""), bill(amount_due="abc"), bill(amount_due="-5"), bill(due_date="11/19/2026"), bill(freq_value=0),
            bill(freq_value=500), bill(freq_unit="decade"), bill(account_id=99999), bill(account_id="x")):
    assert J(a, ta, "post", "/api/bills", bad).status_code == 400, bad
assert J(a, ta, "post", "/api/bills", "junk").status_code == 400

# ---------------- mark as paid rolls the date forward and remembers the payment
p = J(a, ta, "post", f"/api/bills/{yt}/pay", {"paid_date": "2026-11-09", "amount": "29.22"}).get_json()
assert p["due_date"] == "2026-12-19" and p["last_paid_date"] == "2026-11-09" and p["last_paid_amount"] == "29.22"
assert p["next_due"] == "2027-01-19"
h = J(a, ta, "get", f"/api/bills/{yt}/payments").get_json()
assert len(h) == 1 and h[0]["amount"] == "29.22" and h[0]["due_before"] == "2026-11-19"
# undo puts everything back
u = J(a, ta, "post", f"/api/bills/{yt}/undo").get_json()
assert u["due_date"] == "2026-11-19" and u["last_paid_date"] is None and J(a, ta, "get", f"/api/bills/{yt}/payments").get_json() == []
assert J(a, ta, "post", f"/api/bills/{yt}/undo").status_code == 400
# paying without advancing keeps the due date
q = J(a, ta, "post", f"/api/bills/{yt}/pay", {"paid_date": "2026-11-09", "amount": "10", "advance": False}).get_json()
assert q["due_date"] == "2026-11-19"
J(a, ta, "post", f"/api/bills/{yt}/undo")
assert J(a, ta, "post", f"/api/bills/{yt}/pay", {"paid_date": "nope", "amount": "1"}).status_code == 400
assert J(a, ta, "post", f"/api/bills/{yt}/pay", {"paid_date": "2026-11-09", "amount": "x"}).status_code == 400

# ---------------- tracked balance goes down with each payment (and back up on undo)
note = J(a, ta, "post", "/api/bills", bill(name="Truck Note", account_id=dug, amount_due="500", due_date="2026-12-15",
                                         track_balance=True, balance="7,441.00")).get_json()
assert note["balance"] == "7441.00"
n1 = J(a, ta, "post", f"/api/bills/{note['id']}/pay", {"paid_date": "2026-11-09", "amount": "500"}).get_json()
assert n1["balance"] == "6941.00" and n1["due_date"] == "2027-01-15"
n2 = J(a, ta, "post", f"/api/bills/{note['id']}/pay", {"paid_date": "2026-12-09", "amount": "500.50"}).get_json()
assert n2["balance"] == "6440.50"
assert J(a, ta, "post", f"/api/bills/{note['id']}/undo").get_json()["balance"] == "6941.00"
# negative balances (a credit) are kept as entered
neg = J(a, ta, "post", "/api/bills", bill(name="Home Depot", amount_due="29", due_date="2026-12-12", track_balance=True, balance="($646.93)")).get_json()
assert neg["balance"] == "-646.93"
# a bill without tracking hides any balance
assert J(a, ta, "post", "/api/bills", bill(name="Plain", balance="55")).get_json()["balance"] is None

# ---------------- month-end bills do not drift
me = J(a, ta, "post", "/api/bills", bill(name="Month end", due_date="2027-01-31")).get_json()["id"]
assert J(a, ta, "post", f"/api/bills/{me}/pay", {"paid_date": "2027-01-31", "amount": "1"}).get_json()["due_date"] == "2027-02-28"
assert J(a, ta, "post", f"/api/bills/{me}/pay", {"paid_date": "2027-02-28", "amount": "1"}).get_json()["due_date"] == "2027-03-31"

# ---------------- edit / delete / account in use
e = J(a, ta, "put", f"/api/bills/{yt}", bill(amount_due="31.00", account_id=dug)).get_json()
assert e["amount_due"] == "31.00" and e["account"] == "DuGood"
assert J(a, ta, "delete", f"/api/accounts/{dug}").status_code == 400            # still in use
assert J(a, ta, "put", f"/api/accounts/{dug}", {"name": "Du Good"}).status_code == 200
assert J(a, ta, "delete", f"/api/bills/{yt}").status_code == 200

# ---------------- per-user isolation
other = J(a, ta, "post", "/api/bills", bill(name="Private")).get_json()["id"]
assert J(b, tb, "get", "/api/bills").get_json() == []
for m, url, body in (("put", f"/api/bills/{other}", bill()), ("delete", f"/api/bills/{other}", None),
                     ("post", f"/api/bills/{other}/pay", {"paid_date": "2026-11-09", "amount": "1"}),
                     ("get", f"/api/bills/{other}/payments", None), ("post", f"/api/bills/{other}/undo", None)):
    assert J(b, tb, m, url, body).status_code == 404, (m, url)
assert J(b, tb, "post", "/api/bills", bill(account_id=cap)).status_code == 400   # can't use Alice's account
assert J(b, tb, "delete", f"/api/accounts/{cap}").status_code == 404

# ---------------- import: accepts names, creates accounts, skips duplicates, all-or-nothing
imp = {"bills": [
    {"name": "Klarna", "account": "Capital One", "amount_due": "$32.16", "due_date": "2026-11-16", "freq_value": 1, "freq_unit": "Month",
     "last_paid_date": "2026-10-22", "last_paid_amount": "35.00"},
    {"name": "Ford SUV", "account": "New Bank", "amount_due": "775.00", "due_date": "2026-12-16", "track_balance": True, "balance": "15,384.88"},
]}
r = J(b, tb, "post", "/api/import", imp).get_json()
assert r == {"added": 2, "skipped": 0}
assert {x["name"] for x in J(b, tb, "get", "/api/accounts").get_json()} == {"Capital One", "New Bank"}
assert J(b, tb, "post", "/api/import", imp).get_json() == {"added": 0, "skipped": 2}
mixed = {"bills": [{"name": "Good one", "due_date": "2026-12-01"}, {"name": "Bad one", "due_date": "garbage"}]}
assert J(b, tb, "post", "/api/import", mixed).status_code == 400
assert "Good one" not in [x["name"] for x in J(b, tb, "get", "/api/bills").get_json()]       # nothing half-imported
assert J(b, tb, "post", "/api/import", {"bills": []}).status_code == 400
assert J(a, ta, "get", "/api/bills").get_json() and all(x["name"] != "Klarna" for x in J(a, ta, "get", "/api/bills").get_json())

# ---------------- CSV export is safe for spreadsheets
J(b, tb, "post", "/api/bills", bill(name="=HYPERLINK(\"x\")", account_id=None, due_date="2026-12-20"))
csvtext = b.get("/export.csv").get_data(as_text=True)
assert csvtext.startswith("Bill Name,From Account") and "Klarna" in csvtext and "'=HYPERLINK" in csvtext
assert "Klarna" not in a.get("/export.csv").get_data(as_text=True)

# ---------------- login lockout
c = client(); t = token(c)
for _ in range(5):
    c.post("/login", data={"username": "alice", "password": "nope", "csrf_token": t})
assert b"Too many attempts" in c.post("/login", data={"username": "alice", "password": "correct-horse-1", "csrf_token": t}).data
assert "Strict-Transport-Security" not in client().get("/login").headers
A.app.config["SESSION_COOKIE_SECURE"] = True
assert "max-age" in client().get("/login").headers["Strict-Transport-Security"]
A.app.config["SESSION_COOKIE_SECURE"] = False
print("all backend checks passed")
