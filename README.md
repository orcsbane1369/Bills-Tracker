# Recurring Bills Tracker

A web version of the desktop recurring-bills program. Flask + SQLite, one screen, runs as a Docker container on Unraid
behind a Cloudflare Tunnel. Built and updated the same way as the Work Hours app.

## What it does
- One screen lists every bill with its account, amount, due date, last paid date, frequency, next due date, last
  amount paid and (for tracked bills) the balance left.
- **Date Due colors:** red = past due, yellow = due within 14 days, green = more than 14 days out. The summary cards at
  the top add up each group. Colors use the date in your browser.
- **Add New Bill / Update Selected Bill:** a pop-up form. Double-click a row to edit it.
- **Mark as Paid:** records the date and amount, moves the due date ahead by the bill's frequency (every 14 days, 1 month,
  3 months, 1 year...) and, for bills with **Track Remaining**, lowers the balance by the amount paid.
  Month-end bills keep their day (due on the 31st goes Jan 31, Feb 28, Mar 31).
- **Payment History:** every payment for a bill, with **Undo latest payment** that restores the due date, last-paid
  info and balance.
- **Manage Accounts:** the accounts you pay from (names only, no balances).
- **Import Bills:** load a `.json` or `.csv` file. You see the bills first, and existing names are skipped, so it is safe
  to run twice. **Export CSV** downloads the same columns (a handy backup; it can be imported again).
- Click a column heading to sort. The search box and account filter narrow the list.

## Security
Passwords are hashed, sessions are HttpOnly + SameSite, every write needs a CSRF token, login locks for 5 minutes after
5 failures, and every query is limited to the signed-in user. The first account can always be created; after that
sign-ups are closed unless `BT_ALLOW_REGISTRATION=1`. Your bill data is never part of this repository.

## Run locally
    python -m venv .venv
    .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate  (Mac/Linux)
    pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000. Data lives in `instance/bills.db` (one file; back it up by copying it).

## Settings (environment variables)
- `BT_ALLOW_REGISTRATION=0` extra sign-ups closed (1 = open). `BT_REGISTER_CODE=` invitation code when open.
- `BT_HTTPS=1` Secure cookies and HSTS (needed behind the Cloudflare Tunnel).
- `BT_BEHIND_PROXY=1` trust one proxy hop. `BT_CLIENT_IP_HEADER=CF-Connecting-IP` real visitor address for lockouts.
- `BT_INSTANCE=/data` folder for the database and session key (the Docker volume). `BT_DB=path` use another file.
- Docker/Unraid: `BT_PORT` (host port, default 5006) and `BT_DATA_DIR` live in `.env` (copy `.env.example`).

## Deploy on Unraid
GitHub builds the image on every push (`.github/workflows/docker.yml`) and publishes
`ghcr.io/orcsbane1369/bills-tracker:latest` (set the package to **Public** once under your profile > Packages).

Add it in the Docker tab with **Add Container**:
- Repository `ghcr.io/orcsbane1369/bills-tracker:latest`, network bridge
- Port: host 5006 -> container 5000
- Path: `/mnt/user/appdata/billstracker` -> `/data`
- Variables: `BT_BEHIND_PROXY=1`, `BT_HTTPS=1`, `BT_CLIENT_IP_HEADER=CF-Connecting-IP`, `BT_ALLOW_REGISTRATION=0`
- Extra Parameters: `--user 99:100 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`
- Icon URL: `https://raw.githubusercontent.com/orcsbane1369/Bills-Tracker/main/static/icon.png`

(Or use `docker-compose.yml` with Compose Manager.) Point a Cloudflare Tunnel public hostname at `http://<unraid-ip>:5006`.

To update after a push: Docker tab > **Check for Updates** > **apply update**.

## Tests
    python test_schedule.py    # due-date math
    python test_app.py         # login, CSRF, per-user isolation, pay/undo, balances, import, export
