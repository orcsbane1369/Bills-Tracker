"""Recurring Bills Tracker - Flask + SQLite. Run: python app.py  ->  http://127.0.0.1:5000"""
import csv
import io
import ipaddress
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime
from decimal import Decimal as D, ROUND_HALF_UP, InvalidOperation
from functools import wraps

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from schedule import UNITS, add_interval

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.environ.get("BT_INSTANCE") or os.path.join(BASE, "instance")   # database + secret key live here
os.makedirs(INSTANCE, exist_ok=True)
DB_PATH = os.environ.get("BT_DB") or os.path.join(INSTANCE, "bills.db")
# Sign-ups are always open for the very first account; after that only when this is 1.
ALLOW_REGISTRATION = os.environ.get("BT_ALLOW_REGISTRATION", "0") == "1"
REGISTER_CODE = os.environ.get("BT_REGISTER_CODE", "")
BEHIND_PROXY = os.environ.get("BT_BEHIND_PROXY", "0") == "1"
CLIENT_IP_HEADER = os.environ.get("BT_CLIENT_IP_HEADER", "")


def _secret():
    path = os.path.join(INSTANCE, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return open(path).read().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("BT_SECRET_KEY") or _secret(),
    # A different cookie name than the Work Hours app: browsers share cookies across ports on the same address.
    SESSION_COOKIE_NAME="bills_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("BT_HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    MAX_CONTENT_LENGTH=1024 * 1024,
)


def apply_proxy_fix(flask_app):
    """Trust ONE proxy hop for the client IP, scheme and host (only when the app is reachable only through it)."""
    flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1)


if BEHIND_PROXY:
    apply_proxy_fix(app)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL COLLATE NOCASE, UNIQUE(user_id, name));
CREATE TABLE IF NOT EXISTS bills(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  amount_due_c INTEGER NOT NULL DEFAULT 0,
  due_date TEXT NOT NULL,
  freq_value INTEGER NOT NULL DEFAULT 1,
  freq_unit TEXT NOT NULL DEFAULT 'month' CHECK(freq_unit IN ('day','week','month','year')),
  anchor_day INTEGER NOT NULL DEFAULT 1,
  last_paid_date TEXT, last_paid_amount_c INTEGER,
  track_balance INTEGER NOT NULL DEFAULT 0, balance_c INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_bills_user_due ON bills(user_id, due_date);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paid_date TEXT NOT NULL, amount_c INTEGER NOT NULL,
  due_before TEXT NOT NULL, last_paid_date_before TEXT, last_paid_amount_before_c INTEGER,
  balance_before_c INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_payments_bill ON payments(bill_id, paid_date);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)


init_db()

# ---------------------------------------------------------------- security helpers


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Bad CSRF token")


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
        "form-action 'self'")
    if request.path.startswith("/api/") or request.path.endswith(".csv"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="Not signed in"), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


_fails = {}  # key -> [count, locked_until]


def client_ip():
    if CLIENT_IP_HEADER:
        raw = (request.headers.get(CLIENT_IP_HEADER) or "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.remote_addr


def locked(key):
    f = _fails.get(key)
    return bool(f and f[1] > time.time())


def record_fail(key, limit=5):
    f = _fails.setdefault(key, [0, 0])
    f[0] += 1
    if f[0] >= limit:
        f[1] = time.time() + 300
        f[0] = 0


def registration_open():
    if ALLOW_REGISTRATION:
        return True
    return db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0   # the first account can always be made


# ---------------------------------------------------------------- auth pages

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        key = (client_ip(), u.lower())
        acct = ("account", u.lower())
        if locked(key) or locked(acct):
            error = "Too many attempts. Try again in a few minutes."
        else:
            row = db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                _fails.pop(key, None)
                _fails.pop(acct, None)
                session.clear()
                session["uid"] = row["id"]
                session["username"] = row["username"]
                session.permanent = True
                return redirect(url_for("index"))
            record_fail(key)
            record_fail(acct, limit=15)
            error = "Invalid username or password."
    return render_template("login.html", error=error, can_register=registration_open())


@app.route("/register", methods=["GET", "POST"])
def register():
    if not registration_open():
        abort(404)
    error = None
    first = db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    need_code = bool(REGISTER_CODE) and not first
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        reg_key = (client_ip(), "register")
        if locked(reg_key):
            error = "Too many attempts. Try again in a few minutes."
        elif need_code and not secrets.compare_digest(request.form.get("code", "").encode(), REGISTER_CODE.encode()):
            record_fail(reg_key)
            error = "That invitation code is not valid."
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", u):
            error = "Username: 3-32 letters, numbers, _ . -"
        elif len(p) < 10:
            error = "Password must be at least 10 characters."
        elif p != request.form.get("confirm", ""):
            error = "Passwords do not match."
        else:
            try:
                cur = db().execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                                   (u, generate_password_hash(p)))
                db().commit()
                session.clear()
                session["uid"] = cur.lastrowid
                session["username"] = u
                session.permanent = True
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                error = "That username is taken."
    return render_template("register.html", error=error, need_code=need_code, first=first)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session["username"])


# ---------------------------------------------------------------- helpers

def uid():
    return session["uid"]


def jbody():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "JSON body required")
    return data


def cents(v, field, lo=None, hi=None):
    """Money text -> whole cents. Accepts $ and commas, and (1.50) as negative."""
    s = str(v if v is not None else "").strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        x = D(s)
    except InvalidOperation:
        raise ValueError("%s must be a number" % field)
    if not x.is_finite():
        raise ValueError("%s must be a number" % field)
    if neg:
        x = -x
    c = int((x * 100).quantize(D(1), ROUND_HALF_UP))
    if (lo is not None and c < lo * 100) or (hi is not None and c > hi * 100):
        raise ValueError("%s is out of range" % field)
    return c


def money(c):
    if c is None:
        return None
    return "%s%d.%02d" % ("-" if c < 0 else "", abs(c) // 100, abs(c) % 100)


def iso(v, field):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("%s must be a date (YYYY-MM-DD)" % field)


@app.errorhandler(ValueError)
def bad_value(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(400)
def bad_request(e):
    if request.path.startswith("/api/"):
        return jsonify(error=getattr(e, "description", "Bad request")), 400
    return e


# ---------------------------------------------------------------- accounts

def acct_name(v):
    n = " ".join(str(v or "").split())
    if not n or len(n) > 60:
        raise ValueError("An account name needs 1 to 60 characters")
    return n


@app.get("/api/accounts")
@login_required
def list_accounts():
    rows = db().execute("""SELECT a.id, a.name, COUNT(b.id) AS bills FROM accounts a
                           LEFT JOIN bills b ON b.account_id=a.id WHERE a.user_id=? GROUP BY a.id ORDER BY a.name""",
                        (uid(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/accounts")
@login_required
def add_account():
    name = acct_name(jbody().get("name"))
    try:
        cur = db().execute("INSERT INTO accounts(user_id,name) VALUES(?,?)", (uid(), name))
        db().commit()
    except sqlite3.IntegrityError:
        raise ValueError("You already have an account named %s" % name)
    return jsonify(id=cur.lastrowid, name=name)


@app.put("/api/accounts/<int:i>")
@login_required
def rename_account(i):
    name = acct_name(jbody().get("name"))
    try:
        cur = db().execute("UPDATE accounts SET name=? WHERE id=? AND user_id=?", (name, i, uid()))
        db().commit()
    except sqlite3.IntegrityError:
        raise ValueError("You already have an account named %s" % name)
    if not cur.rowcount:
        abort(404)
    return jsonify(id=i, name=name)


@app.delete("/api/accounts/<int:i>")
@login_required
def delete_account(i):
    row = db().execute("SELECT id FROM accounts WHERE id=? AND user_id=?", (i, uid())).fetchone()
    if not row:
        abort(404)
    n = db().execute("SELECT COUNT(*) FROM bills WHERE account_id=?", (i,)).fetchone()[0]
    if n:
        raise ValueError("%d bill%s still use this account. Move them to another account first." % (n, "" if n == 1 else "s"))
    db().execute("DELETE FROM accounts WHERE id=?", (i,))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- bills

BILL_SELECT = """SELECT b.*, a.name AS account FROM bills b LEFT JOIN accounts a ON a.id=b.account_id"""


def bill_json(r):
    due = date.fromisoformat(r["due_date"])
    return {
        "id": r["id"], "name": r["name"], "account_id": r["account_id"], "account": r["account"] or "",
        "amount_due": money(r["amount_due_c"]), "due_date": r["due_date"],
        "next_due": add_interval(due, r["freq_value"], r["freq_unit"], r["anchor_day"]).isoformat(),
        "freq_value": r["freq_value"], "freq_unit": r["freq_unit"],
        "last_paid_date": r["last_paid_date"], "last_paid_amount": money(r["last_paid_amount_c"]),
        "track_balance": bool(r["track_balance"]),
        "balance": money(r["balance_c"]) if r["track_balance"] else None,
    }


def own_bill(i):
    row = db().execute(BILL_SELECT + " WHERE b.id=? AND b.user_id=?", (i, uid())).fetchone()
    if not row:
        abort(404)
    return row


def resolve_account(b):
    """Account id from `account_id`, or from `account` (a name; created on the fly for imports)."""
    if b.get("account_id") not in (None, ""):
        try:
            aid = int(b["account_id"])
        except (TypeError, ValueError):
            raise ValueError("Choose an account from the list")
        if not db().execute("SELECT 1 FROM accounts WHERE id=? AND user_id=?", (aid, uid())).fetchone():
            raise ValueError("Choose an account from the list")
        return aid
    name = " ".join(str(b.get("account") or "").split())
    if not name:
        return None
    row = db().execute("SELECT id FROM accounts WHERE user_id=? AND name=?", (uid(), name)).fetchone()
    if row:
        return row["id"]
    return db().execute("INSERT INTO accounts(user_id,name) VALUES(?,?)", (uid(), acct_name(name))).lastrowid


def clean_bill(b):
    name = " ".join(str(b.get("name") or "").split())
    if not name or len(name) > 80:
        raise ValueError("A bill name needs 1 to 80 characters")
    unit = str(b.get("freq_unit") or "month").lower()
    if unit not in UNITS:
        raise ValueError("Frequency unit must be day, week, month or year")
    try:
        fv = b.get("freq_value")
        value = int(str(1 if fv is None or str(fv).strip() == "" else fv).strip())
    except ValueError:
        raise ValueError("Frequency must be a whole number")
    if not 1 <= value <= 365:
        raise ValueError("Frequency must be between 1 and 365")
    due = iso(b.get("due_date"), "Date due")
    track = bool(b.get("track_balance"))
    balance = cents(b.get("balance"), "Balance left", -10**7, 10**8) if track and str(b.get("balance") or "").strip() else (0 if track else None)
    lp_date = iso(b["last_paid_date"], "Date paid") if b.get("last_paid_date") else None
    lp_amt = cents(b.get("last_paid_amount"), "Paid previously", 0, 10**7) if str(b.get("last_paid_amount") or "").strip() else None
    return {"name": name, "amount_due_c": cents(b.get("amount_due") or 0, "Amount due", 0, 10**7), "due_date": due,
            "freq_value": value, "freq_unit": unit, "anchor_day": int(due[8:10]),
            "last_paid_date": lp_date, "last_paid_amount_c": lp_amt, "track_balance": int(track), "balance_c": balance}


@app.get("/api/bills")
@login_required
def list_bills():
    rows = db().execute(BILL_SELECT + " WHERE b.user_id=? ORDER BY b.due_date, b.name COLLATE NOCASE", (uid(),)).fetchall()
    return jsonify([bill_json(r) for r in rows])


def _insert_bill(b):
    v = clean_bill(b)
    v["account_id"] = resolve_account(b)
    cols = ["user_id"] + list(v)
    cur = db().execute("INSERT INTO bills(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))),
                       [uid()] + [v[k] for k in v])
    return cur.lastrowid


@app.post("/api/bills")
@login_required
def create_bill():
    i = _insert_bill(jbody())
    db().commit()
    return jsonify(bill_json(own_bill(i)))


@app.put("/api/bills/<int:i>")
@login_required
def update_bill(i):
    own_bill(i)
    b = jbody()
    v = clean_bill(b)
    v["account_id"] = resolve_account(b)
    db().execute("UPDATE bills SET %s WHERE id=?" % ",".join("%s=?" % k for k in v), [v[k] for k in v] + [i])
    db().commit()
    return jsonify(bill_json(own_bill(i)))


@app.delete("/api/bills/<int:i>")
@login_required
def delete_bill(i):
    own_bill(i)
    db().execute("DELETE FROM bills WHERE id=?", (i,))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/bills/<int:i>/pay")
@login_required
def pay_bill(i):
    r = own_bill(i)
    b = jbody()
    paid_date = iso(b.get("paid_date"), "Date paid")
    amount = cents(b.get("amount"), "Amount paid", 0, 10**7)
    advance = b.get("advance", True) is not False
    due = date.fromisoformat(r["due_date"])
    new_due = add_interval(due, r["freq_value"], r["freq_unit"], r["anchor_day"]).isoformat() if advance else r["due_date"]
    balance = r["balance_c"]
    if r["track_balance"]:
        balance = (balance or 0) - amount
    db().execute("""INSERT INTO payments(bill_id,user_id,paid_date,amount_c,due_before,last_paid_date_before,
                    last_paid_amount_before_c,balance_before_c) VALUES(?,?,?,?,?,?,?,?)""",
                 (i, uid(), paid_date, amount, r["due_date"], r["last_paid_date"], r["last_paid_amount_c"], r["balance_c"]))
    db().execute("UPDATE bills SET due_date=?, last_paid_date=?, last_paid_amount_c=?, balance_c=? WHERE id=?",
                 (new_due, paid_date, amount, balance, i))
    db().commit()
    return jsonify(bill_json(own_bill(i)))


@app.get("/api/bills/<int:i>/payments")
@login_required
def bill_payments(i):
    own_bill(i)
    rows = db().execute("SELECT * FROM payments WHERE bill_id=? ORDER BY paid_date DESC, id DESC", (i,)).fetchall()
    return jsonify([{"id": p["id"], "paid_date": p["paid_date"], "amount": money(p["amount_c"]),
                     "due_before": p["due_before"]} for p in rows])


@app.post("/api/bills/<int:i>/undo")
@login_required
def undo_payment(i):
    """Reverse the most recent payment: restores the due date, last-paid info and the tracked balance."""
    own_bill(i)
    p = db().execute("SELECT * FROM payments WHERE bill_id=? ORDER BY id DESC LIMIT 1", (i,)).fetchone()
    if not p:
        raise ValueError("There is no payment to undo")
    db().execute("UPDATE bills SET due_date=?, last_paid_date=?, last_paid_amount_c=?, balance_c=? WHERE id=?",
                 (p["due_before"], p["last_paid_date_before"], p["last_paid_amount_before_c"], p["balance_before_c"], i))
    db().execute("DELETE FROM payments WHERE id=?", (p["id"],))
    db().commit()
    return jsonify(bill_json(own_bill(i)))


# ---------------------------------------------------------------- import / export

@app.post("/api/import")
@login_required
def import_bills():
    """Add bills from a JSON file: {"bills": [{name, account, amount_due, due_date, freq_value, freq_unit, ...}]}.
    A bill whose name already exists is skipped, so importing twice does not duplicate anything."""
    data = jbody()
    items = data.get("bills")
    if not isinstance(items, list) or not items or len(items) > 1000:
        raise ValueError("The file has no bills to import")
    have = {r["name"].lower() for r in db().execute("SELECT name FROM bills WHERE user_id=?", (uid(),))}
    added = skipped = 0
    try:
        for n, b in enumerate(items, 1):
            if not isinstance(b, dict):
                raise ValueError("Bill %d is not valid" % n)
            nm = " ".join(str(b.get("name") or "").split()).lower()
            if nm in have:
                skipped += 1
                continue
            try:
                _insert_bill(b)
            except ValueError as e:
                raise ValueError("Bill %d (%s): %s" % (n, nm or "no name", e))
            have.add(nm)
            added += 1
    except ValueError:
        db().rollback()
        raise
    db().commit()
    return jsonify(added=added, skipped=skipped)


def _csv_safe(s):
    """Spreadsheet programs run text that starts with = + - @ as a formula, so prefix such names with a quote."""
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@app.get("/export.csv")
@login_required
def export_csv():
    rows = db().execute(BILL_SELECT + " WHERE b.user_id=? ORDER BY b.due_date, b.name COLLATE NOCASE", (uid(),)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Bill Name", "From Account", "Amount Due", "Date Due", "Freq Value", "Freq Unit", "Next Due",
                "Date Paid", "Paid Previously", "Track Remaining", "Amount Left"])
    for r in rows:
        j = bill_json(r)
        w.writerow([_csv_safe(j["name"]), _csv_safe(j["account"]), j["amount_due"], j["due_date"], j["freq_value"], j["freq_unit"],
                    j["next_due"], j["last_paid_date"] or "", j["last_paid_amount"] or "",
                    "yes" if j["track_balance"] else "no", j["balance"] or ""])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=bills-%s.csv" % date.today().isoformat()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
