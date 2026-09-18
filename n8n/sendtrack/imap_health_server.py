"""The IMAP health check's checker: imap_preflight.py behind one internal URL.

    GET http://imap-health:8765/check      (from inside the compose network only)

Runs imap_preflight.py -- the same connection logic, unchanged, with its opt-in
--ids-json flag -- and answers with one JSON object: did a fresh read-only IMAP
session work, and which INBOX and Sent messages landed in the last HOURS hours.
The "Send & Track - IMAP Health" workflow compares those Message-IDs with what
Mailbox Watch recorded (Section 9, Workflow 6).

It exists because the n8n image has no Python and n8n 2.x excludes the Execute
Command node by default. It runs as the `imap-health` service in
docker-compose.yml, with no published port.

The mailbox credentials arrive as environment variables -- only the ones the
pre-flight needs -- and are written to a private file on the container's tmpfs,
because the pre-flight reads a file. Nothing here prints them.

Checks never overlap (one request at a time) and never run more often than
MIN_INTERVAL_S: anything calling faster gets the last answer back instead of
logging in to the mailbox again.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PREFLIGHT = os.path.join(HERE, "imap_preflight.py")
MASTER_REF = os.environ.get("NOVASCOUT_MASTER_REF", os.path.join(HERE, "..", "..", "NovaScout_MasterRef.md"))

PORT = 8765
HOURS = 48
MIN_INTERVAL_S = 60
TIMEOUT_S = 120
ENV_KEYS = ("NOVASCOUT_MAILBOX_ADDRESS", "NOVASCOUT_MAILBOX_PASSWORD", "NOVASCOUT_IMAP_HOST", "NOVASCOUT_IMAP_PORT")


def _tail(text, n=1500):
    text = (text or "").strip()
    return text if len(text) <= n else "..." + text[-n:]


def parse_result(returncode, out, err, ids):
    """One pre-flight run -> the answer. `ids` is the parsed --ids-json file, or
    None when the run did not write one."""
    res = {"ok": False, "status": "error", "stage": None, "detail": None, "hint": None, "note": None,
           "exit_code": returncode, "inbox": [], "sent": [], "hours": HOURS,
           "inbox_no_message_id": 0, "sent_no_message_id": 0, "sent_undated": 0,
           "output_tail": _tail(out)}
    failed = re.search(r"^IMAP CHECK: FAILED at (\S+)\n  ([^\n]*)(?:\n  hint: ([^\n]*))?", out or "", re.M)
    if re.search(r"^IMAP CHECK: PASSED$", out or "", re.M):
        if ids is None:
            res.update(stage="ids", detail="the IMAP check passed but no Message-ID list was written")
            return res
        res.update(ok=True, status="passed")
        for k in ("inbox", "sent", "hours", "inbox_no_message_id", "sent_no_message_id", "sent_undated"):
            if k in ids:
                res[k] = ids[k]
        if returncode != 0:
            # The warm-up report after the IMAP check failed. Not a mailbox
            # problem, so not a health failure -- but visible.
            lines = [ln for ln in (err or out or "").strip().splitlines() if ln.strip()]
            res["note"] = "pre-flight exited %d after the IMAP check passed: %s" % (
                returncode, lines[-1] if lines else "no output")
    elif failed:
        res.update(status="failed", stage=failed.group(1), detail=failed.group(2), hint=failed.group(3))
    elif re.search(r"^IMAP CHECK: NOT RUN", out or "", re.M):
        res.update(status="not-run", stage="credentials",
                   detail=re.search(r"^IMAP CHECK: NOT RUN[^\n]*", out, re.M).group(0))
    else:
        lines = [ln for ln in ((err or "") + "\n" + (out or "")).strip().splitlines() if ln.strip()]
        res.update(stage="preflight", detail="pre-flight exited %d without a result: %s" % (
            returncode, lines[-1] if lines else "no output"))
    return res


def write_env_file(environ):
    fd, path = tempfile.mkstemp(prefix="imap-health-", suffix=".env")
    with io.open(fd, "w", encoding="utf-8") as fh:
        for k in ENV_KEYS:
            if environ.get(k):
                fh.write("%s=%s\n" % (k, environ[k]))
    os.chmod(path, 0o600)
    return path


def run_check(env_file):
    fd, ids_path = tempfile.mkstemp(prefix="imap-health-", suffix=".json")
    os.close(fd)
    os.unlink(ids_path)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "NOVASCOUT_ENV_FILE": env_file, "NOVASCOUT_MASTER_REF": MASTER_REF}
    started = time.time()
    try:
        p = subprocess.run([sys.executable, PREFLIGHT, "--ids-json", ids_path, "--ids-hours", str(HOURS)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=TIMEOUT_S,
                           encoding="utf-8", errors="replace")
        ids = None
        if os.path.isfile(ids_path):
            with io.open(ids_path, encoding="utf-8") as fh:
                ids = json.load(fh)
        res = parse_result(p.returncode, p.stdout, p.stderr, ids)
    except subprocess.TimeoutExpired as e:
        partial = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        res = parse_result(-1, partial, "", None)
        res.update(stage="timeout", detail="the pre-flight did not finish within %d s" % TIMEOUT_S)
    finally:
        if os.path.isfile(ids_path):
            os.unlink(ids_path)
    res["duration_ms"] = int((time.time() - started) * 1000)
    res["checked_at"] = datetime.now(timezone.utc).isoformat()
    res["cached"] = False
    return res


class Handler(BaseHTTPRequestHandler):
    env_file = None
    last = None
    last_at = 0.0

    def do_GET(self):
        if self.path.split("?")[0] != "/check":
            self.send_error(404)
            return
        cls = type(self)
        if cls.last is not None and time.time() - cls.last_at < MIN_INTERVAL_S:
            res = dict(cls.last, cached=True)
        else:
            res = run_check(cls.env_file)
            cls.last, cls.last_at = res, time.time()
            print("%s check: %s%s (%d ms; %d INBOX, %d Sent)" % (
                res["checked_at"], res["status"], " at " + res["stage"] if res["stage"] else "",
                res["duration_ms"], len(res["inbox"]), len(res["sent"])), flush=True)
        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    Handler.env_file = write_env_file(os.environ)
    missing = [k for k in ENV_KEYS[:3] if not os.environ.get(k)]
    print("imap-health listening on :%d -- credentials: %s" % (
        PORT, "MISSING " + ", ".join(missing) if missing else "set"), flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
