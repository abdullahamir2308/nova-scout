"""Workflow 6 pre-flight: prove the mailbox's IMAP works, then read the real
send history out of its Sent folder and derive the warm-up state from it --
the derivation Mailbox Watch's Sent mirror and the send path keep current.

Credentials come from the repo's .env (gitignored) and are never printed.
Every mailbox operation is read-only: folders are opened with EXAMINE and
headers fetched with BODY.PEEK, so nothing is marked seen, moved or deleted.

A message counts toward the warm-up when any recipient is outside the
mailbox's own domain and is not NOVASCOUT_OPERATOR_EMAIL -- the reply
notifications go there (Section 9). This script reads the address from .env;
the workflow reads the copy sync_settings.py writes to the settings table.

    python n8n/sendtrack/imap_preflight.py
    python n8n/sendtrack/imap_preflight.py --ids-json out.json [--ids-hours 48]

--ids-json is for the IMAP health check (imap_health_server.py): in the same
session, with the same read-only EXAMINE / BODY.PEEK reads, it also writes the
Message-IDs of every INBOX and Sent message that landed in the last --ids-hours
hours, so the health check can compare them with what Mailbox Watch recorded.
Without the flag nothing is fetched or printed differently.

Exit 0 = every check passed. Exit 1 = a check failed. Exit 2 = not run
(credentials missing from .env).
"""
import email
import email.header
import email.utils
import imaplib
import io
import json
import os
import re
import socket
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(HERE, "..", "..", ".env"))
MASTER_REF = os.environ.get("NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md"))

REQUIRED = ["NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_MAILBOX_PASSWORD", "NOVASCOUT_IMAP_HOST"]

# The warm-up ceiling is sends per day from one mailbox, so "day" needs one
# fixed clock -- the sender's. Asia/Karachi is n8n's GENERIC_TIMEZONE in
# docker-compose.yml and has no DST, so a fixed +05:00 is exact.
SENDER_TZ = timezone(timedelta(hours=5), "PKT")

IDS_JSON = None
IDS_HOURS = 48
_argv = sys.argv[1:]
while _argv:
    _a = _argv.pop(0)
    if _a == "--ids-json" and _argv:
        IDS_JSON = _argv.pop(0)
    elif _a == "--ids-hours" and _argv:
        IDS_HOURS = int(_argv.pop(0))
    else:
        raise SystemExit("usage: imap_preflight.py [--ids-json PATH [--ids-hours N]]")


def load_env(path):
    vals = {}
    with io.open(path, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            vals[k.strip()] = v
    return vals


def load_warmup(path):
    """Section 5's warm-up table, parsed rather than copied."""
    with io.open(path, encoding="utf-8") as fh:
        doc = fh.read()
    rows = re.findall(r"^\| Week (\d+)(\+?) \| (\d+)", doc, re.M)
    if not rows:
        raise SystemExit("warm-up table not found in " + path)
    return [(int(w), plus == "+", int(n)) for w, plus, n in rows]


def ceiling_for(week, table):
    for n, plus, cap in table:
        if week == n or (plus and week >= n):
            return cap
    raise ValueError("no warm-up row covers week %d" % week)


def fail(stage, detail, hint=None):
    print("\nIMAP CHECK: FAILED at %s" % stage)
    print("  " + detail)
    if hint:
        print("  hint: " + hint)
    sys.exit(1)


LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\) (?P<delim>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')


def parse_list(line):
    m = LIST_RE.match(line)
    if not m:
        return None
    return m.group("flags").decode("ascii", "replace").split(), m.group("name").decode("utf-8", "replace").strip()


def bare(name):
    return name[1:-1] if len(name) >= 2 and name[0] == name[-1] == '"' else name


def quoted(name):
    return name if name.startswith('"') else '"%s"' % name


def as_utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def message_id(v):
    """Mailbox Watch's key for a message, derived exactly as code_mirror_sent.js
    and code_classify_reply.js derive it: the first <...> token, else the first
    word in brackets. The health check compares these strings as they are."""
    s = (v or "").strip()
    m = re.search(r"<[^<>\s]+>", s)
    if m:
        return m.group(0)
    words = s.split()
    first = re.sub(r"[<>]", "", words[0]) if words else ""
    return "<%s>" % first if first else ""


def header_text(v):
    if not v:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(v))).strip()
    except (ValueError, LookupError, UnicodeError):
        return str(v).strip()


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def imap_date(dt):
    # SEARCH SINCE takes an English month abbreviation whatever the host locale.
    return "%02d-%s-%d" % (dt.day, MONTHS[dt.month - 1], dt.year)


def fetch_internaldates(spec, stage):
    typ, dat = M.fetch(spec, "(INTERNALDATE)")
    if typ != "OK":
        fail(stage, "FETCH INTERNALDATE: %s %r" % (typ, dat))
    out = {}
    for line in dat:
        if not isinstance(line, bytes):
            continue
        m = re.match(rb'(\d+) \(.*INTERNALDATE "([^"]+)"', line)
        if m:
            out[int(m.group(1))] = datetime.strptime(m.group(2).decode(), "%d-%b-%Y %H:%M:%S %z")
    return out


def fetch_headers(spec, fields, stage):
    typ, dat = M.fetch(spec, "(BODY.PEEK[HEADER.FIELDS (%s)])" % fields)
    if typ != "OK":
        fail(stage, "FETCH headers: %s %r" % (typ, dat))
    out = {}
    for part in dat:
        if isinstance(part, tuple):
            m = re.match(rb"(\d+) ", part[0])
            if m:
                out[int(m.group(1))] = email.message_from_bytes(part[1])
    return out


# ---------------------------------------------------------------------------

env = load_env(ENV_PATH)
print("credentials in .env: " + ", ".join(
    "%s=%s" % (k, "set" if env.get(k) else "MISSING") for k in REQUIRED))
missing = [k for k in REQUIRED if not env.get(k)]
if missing:
    print("\nIMAP CHECK: NOT RUN -- add %s to %s" % (", ".join(missing), ENV_PATH))
    sys.exit(2)

addr = env["NOVASCOUT_MAILBOX_ADDRESS"]
host = env["NOVASCOUT_IMAP_HOST"]
port = int(env.get("NOVASCOUT_IMAP_PORT") or 993)
own_domain = addr.rsplit("@", 1)[-1].lower()
operator = env.get("NOVASCOUT_OPERATOR_EMAIL", "").strip().lower()

# 1. TCP + TLS
try:
    M = imaplib.IMAP4_SSL(host, port, timeout=30)
except socket.gaierror as e:
    fail("connect", "%s does not resolve: %s" % (host, e), "check NOVASCOUT_IMAP_HOST")
except (OSError, imaplib.IMAP4.error) as e:
    fail("connect", "%s:%d: %r" % (host, port, e))
print("1. connect  ok  %s:%d over TLS -- greeting: %s"
      % (host, port, M.welcome.decode("utf-8", "replace")[:100]))

# 2. LOGIN. This is the step that proves IMAP access is actually switched on
# for the account -- a plan that merely allows IMAP still refuses the login
# until the per-account toggle is enabled.
try:
    M.login(addr, env["NOVASCOUT_MAILBOX_PASSWORD"])
except imaplib.IMAP4.error as e:
    fail("login", "server refused %s: %s" % (addr, e),
         "usual causes on Zoho: IMAP Access switched off for this account "
         "(Settings > Mail Accounts > IMAP Access); 2FA on and the normal "
         "password used instead of an app-specific password; or the host is "
         "wrong for the account type / data centre")
print("2. login    ok  %s" % addr)
typ, capdat = M.capability()
print("   capabilities: %s" % (capdat[0].decode("ascii", "replace") if typ == "OK" else typ))

# 3. LIST
typ, raw = M.list()
if typ != "OK":
    fail("list", "LIST returned %s %r" % (typ, raw))
boxes = [p for p in (parse_list(x) for x in raw if isinstance(x, bytes)) if p]
print("3. list     ok  %d folders: %s" % (len(boxes), ", ".join(bare(n) for _, n in boxes)))

sent = [n for f, n in boxes if "\\Sent" in f]
how = "RFC 6154 \\Sent flag"
if not sent:
    sent = [n for f, n in boxes if bare(n).lower() in ("sent", "sent items", "sent mail", "sent messages")]
    how = "folder name (server advertises no \\Sent flag)"
if not sent:
    fail("sent-folder", "no folder flagged \\Sent or named like Sent: %r" % [bare(n) for _, n in boxes])
sent_name = sent[0]
print("   Sent folder: %s (found by %s)" % (bare(sent_name), how))

# 4. INBOX -- the folder reply and opt-out detection will poll.
typ, dat = M.select("INBOX", readonly=True)
if typ != "OK":
    fail("inbox", "EXAMINE INBOX: %s %r" % (typ, dat))
inbox_n = int(dat[0])
typ, uns = M.search(None, "UNSEEN")
unseen = len(uns[0].split()) if typ == "OK" and uns and uns[0] else 0
print("4. INBOX    ok  %d messages, %d unseen (EXAMINE, read-only)" % (inbox_n, unseen))

ids_since = datetime.now(timezone.utc) - timedelta(hours=IDS_HOURS)
inbox_recent, inbox_no_id = [], 0
if IDS_JSON:
    # SINCE compares dates, not times, on the server's clock: ask from a day
    # earlier and trim by INTERNALDATE -- when the message landed in the folder.
    typ, found = M.search(None, "SINCE", imap_date(ids_since - timedelta(days=1)))
    if typ != "OK":
        fail("inbox-ids", "SEARCH SINCE: %s %r" % (typ, found))
    seqs = found[0].split() if found and found[0] else []
    if seqs:
        spec = b",".join(seqs).decode("ascii")
        dates = fetch_internaldates(spec, "inbox-ids")
        hdrs = fetch_headers(spec, "MESSAGE-ID FROM SUBJECT", "inbox-ids")
        for seq in sorted(dates):
            at = as_utc(dates[seq])
            if at < ids_since:
                continue
            msg = hdrs.get(seq)
            mid = message_id(msg.get("Message-ID") if msg is not None else "")
            if not mid:
                inbox_no_id += 1
                continue
            inbox_recent.append({"message_id": mid, "at": at.isoformat(),
                                 "from": header_text(msg.get("From")), "subject": header_text(msg.get("Subject"))})

# 5. Sent -- open and read every message's date and recipients.
typ, dat = M.select(quoted(sent_name), readonly=True)
if typ != "OK":
    fail("sent-folder", "EXAMINE %s: %s %r" % (sent_name, typ, dat))
sent_n = int(dat[0])

internal = {}
headers = {}
if sent_n:
    # Two fetches rather than one: servers order INTERNALDATE and the header
    # literal differently in a combined response, and a parse that guesses
    # the order wrong silently drops dates.
    internal = fetch_internaldates("1:*", "sent-fetch")
    headers = fetch_headers("1:*", "DATE TO CC BCC" + (" MESSAGE-ID" if IDS_JSON else ""), "sent-fetch")
M.logout()
print("5. Sent     ok  %d messages, %d dated, %d with headers" % (sent_n, len(internal), len(headers)))

if IDS_JSON:
    sent_recent, sent_no_id, sent_undated = [], 0, 0
    for seq in sorted(internal):
        at = as_utc(internal[seq])
        if at < ids_since:
            continue
        msg = headers.get(seq)
        mid = message_id(msg.get("Message-ID") if msg is not None else "")
        try:
            dated = msg is not None and bool(msg.get("Date")) and email.utils.parsedate_to_datetime(msg["Date"]) is not None
        except (TypeError, ValueError):
            dated = False
        if not mid:
            sent_no_id += 1
        elif not dated:
            # The Sent mirror skips a message with no usable Date header (it
            # counts it as undated), so it would never be found there.
            sent_undated += 1
        else:
            sent_recent.append({"message_id": mid, "at": at.isoformat(), "to": header_text(msg.get("To"))})
    with io.open(IDS_JSON, "w", encoding="utf-8") as fh:
        json.dump({"hours": IDS_HOURS, "since": ids_since.isoformat(),
                   "inbox": inbox_recent, "inbox_no_message_id": inbox_no_id,
                   "sent": sent_recent, "sent_no_message_id": sent_no_id, "sent_undated": sent_undated},
                  fh, ensure_ascii=False)
    print("   last %dh: %d INBOX and %d Sent message(s) by Message-ID -> %s"
          % (IDS_HOURS, len(inbox_recent), len(sent_recent), IDS_JSON))
print("\nIMAP CHECK: PASSED")

# ---------------------------------------------------------------------------
# Send history -> warm-up state
# ---------------------------------------------------------------------------

records = []
disagree = 0
for seq in sorted(set(internal) | set(headers)):
    msg = headers.get(seq)
    hdr_date = None
    if msg is not None and msg.get("Date"):
        try:
            hdr_date = as_utc(email.utils.parsedate_to_datetime(msg["Date"]))
        except (TypeError, ValueError):
            hdr_date = None
    idate = as_utc(internal.get(seq))
    if hdr_date and idate and abs((hdr_date - idate).total_seconds()) > 86400:
        disagree += 1
    when = hdr_date or idate
    rcpts = []
    if msg is not None:
        rcpts = email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", []))
    addrs = set(a.lower().strip("<>") for _, a in rcpts if "@" in a)
    # Mailbox Watch's rule: any recipient outside the own domain that is not
    # the operator's own notification address makes the message count.
    external = any(a.rsplit("@", 1)[-1] != own_domain and a != operator for a in addrs)
    records.append((when, external))

undated = sum(1 for w, _ in records if w is None)
records = [(w.astimezone(SENDER_TZ), ext) for w, ext in records if w is not None]

by_day = Counter(w.date() for w, _ in records)
by_day_ext = Counter(w.date() for w, ext in records if ext)

print("\nSent history by day (%s, the sender's clock):" % SENDER_TZ.tzname(None))
print("  %-12s %5s %9s" % ("date", "total", "external"))
for d in sorted(by_day):
    print("  %-12s %5d %9d" % (d.isoformat(), by_day[d], by_day_ext.get(d, 0)))
print("  messages only to %s%s (not warm-up): %d"
      % (own_domain, " or the operator's notification address" if operator else "",
         sum(1 for _, ext in records if not ext)))
if not operator:
    print("  (NOVASCOUT_OPERATOR_EMAIL is not set in .env -- mail to the operator would count)")
if undated:
    print("  undated messages (no usable Date or INTERNALDATE): %d" % undated)
if disagree:
    print("  messages whose Date header and INTERNALDATE differ by >24h: %d" % disagree)

table = load_warmup(MASTER_REF)
today = datetime.now(SENDER_TZ).date()
print("\nWarm-up table parsed from Section 5: %s"
      % ", ".join("week %d%s=%d" % (n, "+" if p else "", c) for n, p, c in table))

if not by_day_ext:
    print("Warm-up: NOT STARTED -- no send to an external domain in Sent.")
    print("  The first real send starts week 1 (ceiling %d/day)." % ceiling_for(1, table))
    sys.exit(0)

days = sorted(by_day_ext)
first = days[0]
day_index = (today - first).days
week = day_index // 7 + 1
cap = ceiling_for(week, table)
sent_today = by_day_ext.get(today, 0)
gaps = [(b - a).days for a, b in zip(days, days[1:])]
over = [(d, by_day_ext[d], ceiling_for((d - first).days // 7 + 1, table))
        for d in days if by_day_ext[d] > ceiling_for((d - first).days // 7 + 1, table)]

print("Warm-up:")
print("  first external send   %s" % first.isoformat())
print("  today                 %s -> day %d of warm-up, week %d" % (today.isoformat(), day_index + 1, week))
print("  ceiling today         %d/day" % cap)
print("  external sent today   %d  -> %d remaining" % (sent_today, max(cap - sent_today, 0)))
print("  active send days      %d of %d calendar days" % (len(days), day_index + 1))
print("  longest gap           %d days" % (max(gaps) if gaps else 0))
if over:
    print("  days over their week's ceiling: " + ", ".join("%s (%d > %d)" % (d.isoformat(), n, c) for d, n, c in over))
