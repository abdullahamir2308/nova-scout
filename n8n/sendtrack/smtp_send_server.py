"""The threaded sender: one SMTP submission that can carry In-Reply-To and References.

    POST http://smtp-send:8766/send      (from inside the compose network only)
    GET  http://smtp-send:8766/health

WHY THIS EXISTS, measured rather than assumed. A reply has to thread to the
conversation it answers -- Section 9, Workflow 6 records the exact two header
values lead 26's reply needs -- and the Send Email node that every other
outbound message in this project goes through CANNOT set them. Read from the
installed node on 2026-10-09,
n8n-nodes-base/dist/nodes/EmailSend/v2/send.operation.js builds nodemailer's
mailOptions from a fixed field set:

    const mailOptions = { from, to, cc, bcc, subject, replyTo };

plus text/html and attachments. There is no headers option, no inReplyTo and no
references, and its `options` collection offers only appendAttribution,
attachments, ccEmail, bccEmail, allowUnauthorizedCerts and replyTo. nodemailer
itself supports all three; n8n does not pass them. The node's batchSize options
do not help either, and nothing in a Code node can open a socket.

So the reply lane submits through this service instead -- the same decision, for
the same reason, as the `imap-health` sidecar on 2026-09-16: the n8n image has
no Python, and n8n 2.x excludes the Execute Command node by default, which
would hand every Code node shell access. The COLD path is untouched and still
goes through the Send Email node; only `reply/` drafts come here, so a fault in
this file cannot affect a first touch or a follow-up.

WHAT IT ANSWERS WITH is nodemailer's own shape -- accepted, rejected, messageId,
response -- so `code_check_smtp.js` classifies a failure exactly as it does on
the cold path, with no second copy of that logic:

    {"accepted": ["a@b.com"], "rejected": [], "messageId": "<...>", "response": "250 ..."}
    {"accepted": [], "rejected": ["a@b.com"], "response": "550 ...", "error": "..."}

It always answers 200 with that object, errors included, so the HTTP node hands
the text to Check SMTP Result rather than turning it into an n8n error: a
failure after the claim was written must reach the revert branch, never strand
the claim.

NEVER RETRIES. A timeout after the server has accepted the message would send
twice; the n8n node calling this has retryOnFail off for the same reason, and
Send's claim-before-send makes at-most-once the invariant.

The mailbox credentials arrive as environment variables and nothing here prints
them. One request at a time, and no request is served until the From address
matches the configured mailbox -- this service has no authentication of its own,
so it must not be able to send as anyone else. It publishes no port
(build_workflow.py refuses one, as it does for imap-health).
"""
import email.utils
import json
import os
import re
import smtplib
import ssl
import sys
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8766
TIMEOUT_S = 60
MAX_BODY = 256 * 1024

MAILBOX = (os.environ.get("NOVASCOUT_MAILBOX_ADDRESS") or "").strip()
PASSWORD = os.environ.get("NOVASCOUT_MAILBOX_PASSWORD") or ""
SMTP_HOST = (os.environ.get("NOVASCOUT_SMTP_HOST") or "").strip()
SMTP_PORT = int(os.environ.get("NOVASCOUT_SMTP_PORT") or 465)
SENDER_NAME = (os.environ.get("NOVASCOUT_SENDER_NAME") or "").strip()

ADDRESS_RE = re.compile(r"^[^\s@<>(),;:\"']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
MESSAGE_ID_RE = re.compile(r"^<[^<>\s@]+@[^<>\s]+>$")

_lock = threading.Lock()


def _ids(value):
    """A Message-ID list from a string or a list, keeping only well-formed ids.

    A half-parsed header -- a bare local part, a stray comma -- must never reach
    the References line of an outbound message; migration 017 enforces the same
    shape on `inbound_messages.thread_ids` with a CHECK."""
    if value is None:
        return []
    items = value if isinstance(value, list) else re.findall(r"<[^<>\s]+>", str(value))
    out = []
    for item in items:
        token = str(item).strip()
        if MESSAGE_ID_RE.match(token) and token not in out:
            out.append(token)
    return out


def build_message(req):
    """The request -> an EmailMessage, or (None, why)."""
    to = str(req.get("to") or "").strip()
    subject = str(req.get("subject") or "").strip()
    text = req.get("text")
    if not ADDRESS_RE.match(to):
        return None, "not one bare recipient address: %r" % to
    if not subject:
        return None, "no subject"
    if not isinstance(text, str) or not text.strip():
        return None, "no plain-text body"
    # This service holds the mailbox password, so the only identity it may send
    # under is the mailbox's own. A From in the request is checked, not trusted.
    sender = str(req.get("from") or MAILBOX).strip()
    if email.utils.parseaddr(sender)[1].lower() != MAILBOX.lower():
        return None, "from address %r is not this mailbox" % sender

    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((SENDER_NAME, MAILBOX)) if SENDER_NAME else MAILBOX
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=MAILBOX.split("@")[-1])
    # The point of this service. In-Reply-To is the message being answered;
    # References is its chain plus itself, which is what a client needs to place
    # this message in the thread (Section 9, Workflow 6 has the observed values
    # for lead 26). Both are omitted entirely when the caller sends none, rather
    # than guessed at.
    in_reply_to = _ids(req.get("in_reply_to"))
    references = _ids(req.get("references"))
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to[0]
    if references:
        msg["References"] = " ".join(references)
    # Section 5: plain text only. set_content writes text/plain; nothing here
    # ever adds an HTML part, an attachment or a tracking pixel.
    msg.set_content(text)
    return msg, None


def send(req):
    msg, why = build_message(req)
    if why:
        return {"accepted": [], "rejected": [], "messageId": None, "response": None,
                "error": "refused before sending: " + why}
    to = msg["To"]
    out = {"accepted": [], "rejected": [], "messageId": msg["Message-ID"], "response": None, "error": None,
           "in_reply_to": msg.get("In-Reply-To"), "references": msg.get("References")}
    if not (SMTP_HOST and MAILBOX and PASSWORD):
        out["error"] = "refused before sending: SMTP host, mailbox or password is not configured"
        out["messageId"] = None
        return out
    try:
        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_S, context=context)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_S)
        with server:
            if SMTP_PORT != 465:
                server.ehlo()
                server.starttls(context=context)
            server.ehlo()
            server.login(MAILBOX, PASSWORD)
            refused = server.send_message(msg)
            # smtplib raises on a total refusal and returns the per-recipient
            # failures otherwise; with one recipient, a non-empty dict means
            # that address was refused.
            if to in refused:
                code, detail = refused[to]
                out["rejected"] = [to]
                out["response"] = "%s %s" % (code, detail.decode("utf-8", "replace")
                                             if isinstance(detail, bytes) else detail)
                out["error"] = "rejected by server: " + out["response"]
                out["messageId"] = None
            else:
                out["accepted"] = [to]
                # smtplib does not hand back the final 250 text, so this is the
                # honest equivalent rather than an invented server string.
                out["response"] = "250 accepted by %s:%d" % (SMTP_HOST, SMTP_PORT)
    except smtplib.SMTPRecipientsRefused as e:
        first = list(e.recipients.values())[0] if e.recipients else (550, b"recipient refused")
        detail = first[1].decode("utf-8", "replace") if isinstance(first[1], bytes) else first[1]
        out.update(rejected=[to], messageId=None, response="%s %s" % (first[0], detail),
                   error="rejected by server: %s %s" % (first[0], detail))
    except Exception as e:                                    # noqa: BLE001 -- every failure is reported, not raised
        out.update(messageId=None, error="%s: %s" % (type(e).__name__, e))
    return out


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            self.send_error(404)
            return
        self._json({"ok": bool(SMTP_HOST and MAILBOX and PASSWORD), "host": SMTP_HOST, "port": SMTP_PORT,
                    "mailbox": MAILBOX, "at": datetime.now(timezone.utc).isoformat()})

    def do_POST(self):
        if self.path.split("?")[0] != "/send":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._json({"accepted": [], "rejected": [], "messageId": None,
                        "error": "refused before sending: body is %d bytes" % length})
            return
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:                                # noqa: BLE001
            self._json({"accepted": [], "rejected": [], "messageId": None,
                        "error": "refused before sending: body is not JSON (%s)" % e})
            return
        if not isinstance(req, dict):
            self._json({"accepted": [], "rejected": [], "messageId": None,
                        "error": "refused before sending: body is not a JSON object"})
            return
        with _lock:
            res = send(req)
        print("%s send -> %s: %s" % (datetime.now(timezone.utc).isoformat(),
                                     req.get("to"), res.get("error") or res.get("response")), flush=True)
        self._json(res)

    def log_message(self, fmt, *args):
        pass


def main():
    missing = [k for k, v in (("NOVASCOUT_SMTP_HOST", SMTP_HOST), ("NOVASCOUT_MAILBOX_ADDRESS", MAILBOX),
                              ("NOVASCOUT_MAILBOX_PASSWORD", PASSWORD)) if not v]
    print("smtp-send listening on :%d -- %s:%d as %s%s" % (
        PORT, SMTP_HOST or "?", SMTP_PORT, MAILBOX or "?",
        "  MISSING " + ", ".join(missing) if missing else ""), flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
    sys.exit(0)
