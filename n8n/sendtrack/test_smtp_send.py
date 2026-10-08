"""Tests for the reply sender -- smtp_send_server.py (Section 9, Workflow 7).

    python n8n/sendtrack/test_smtp_send.py

This service exists for one reason: a reply has to carry In-Reply-To and
References, and the installed n8n Send Email node cannot set them (that file
quotes the node's own mailOptions). So what is tested here is the message it
builds -- the two headers, the plain-text body, the From it refuses to forge --
and every refusal it can answer with before it opens a socket.

WHAT IS NOT TESTED HERE, stated rather than implied: the SMTP conversation
itself. The service submits over implicit TLS on 465 or STARTTLS otherwise, and
a sink that could stand in for that would need a certificate the service's
default SSL context accepts -- so faking it would prove less than it looks.
The HTTP contract between the workflow and this service IS proven end to end in
the reply dry run, against a sink that captures what would have been submitted;
the first real submission is the first approved reply.

The module is imported, not run: main() is guarded, and every function below is
pure apart from send(), which is only called here on paths that return before
connecting.

Exits non-zero on any failure.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAILBOX = "abdullah@amitrixlabs.com"

# OVERRIDE, never setdefault, and this is not a style point -- it is the lesson
# from a real send. The first version of this file used os.environ.setdefault
# for the SMTP host and the mailbox password, on the assumption that a developer
# shell would not have them. This shell did: the project's .env values are in
# the ambient environment, so the module imported with the REAL Zoho host and
# the real password, and the one call to send() below reached Zoho and
# submitted a message to a real prospect (2026-10-08 22:31 UTC, lead 26). It
# cannot be unsent.
#
# So the credentials are blanked unconditionally before the import, and the
# assertion below refuses to run the suite at all if a host survived -- there is
# no path through this file that may open a socket.
os.environ["NOVASCOUT_MAILBOX_ADDRESS"] = MAILBOX
os.environ["NOVASCOUT_SENDER_NAME"] = "Abdullah Amir"
os.environ["NOVASCOUT_SMTP_HOST"] = ""
os.environ["NOVASCOUT_SMTP_PORT"] = "465"
os.environ["NOVASCOUT_MAILBOX_PASSWORD"] = ""

import smtp_send_server as srv  # noqa: E402  (no side effects: main() is guarded)

if srv.SMTP_HOST or srv.PASSWORD:
    raise SystemExit("REFUSING TO RUN: smtp_send_server imported with a reachable SMTP host (%r) or a "
                     "password set. This suite calls send(); with a real host that submits real mail to a "
                     "real prospect. Blank NOVASCOUT_SMTP_HOST and NOVASCOUT_MAILBOX_PASSWORD."
                     % srv.SMTP_HOST)

PASSED, FAILED = [], []


def check(label, actual, expected):
    if actual == expected:
        PASSED.append(label)
    else:
        FAILED.append((label, "expected %r\n         actual   %r" % (expected, actual)))


OURS = "<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>"
THEIRS = "<01bd01dd562e$5fe83240$1fb896c0$@pharmahungary.com>"
BODY = "Hello,\n\nThank you for the clear answer.\n\nAbdullah Amir\nFounder, Amitrix Labs\n+923178485713"


def req(**over):
    r = {"to": "andras.nogradi@pharmahungary.com",
         "subject": "Re: Oncology and cardiovascular on your site",
         "text": BODY,
         "in_reply_to": THEIRS,
         "references": [OURS, THEIRS]}
    r.update(over)
    return r


# --- the two headers this service exists for ---------------------------------

msg, why = srv.build_message(req())
check("a well-formed request builds a message", why, None)
check("In-Reply-To is the message being answered", msg["In-Reply-To"], THEIRS)
check("References is the chain, space-separated, in order", msg["References"], OURS + " " + THEIRS)
check("... which is exactly what Section 9, Workflow 6 recorded for lead 26",
      (msg["In-Reply-To"], msg["References"]), (THEIRS, OURS + " " + THEIRS))
check("From is the mailbox, with the sender's name", msg["From"], 'Abdullah Amir <%s>' % MAILBOX)
check("To is the one recipient", msg["To"], "andras.nogradi@pharmahungary.com")
check("it carries its own Message-ID, on the mailbox's domain",
      msg["Message-ID"].endswith("@amitrixlabs.com>"), True)
check("Section 5: plain text, one part, no HTML",
      (msg.get_content_type(), msg.is_multipart()), ("text/plain", False))
check("the body is the text, verbatim", msg.get_content().rstrip("\n"), BODY)

# A References chain that arrives as a raw header string, as it is stored.
msg, _ = srv.build_message(req(references=OURS + " " + THEIRS))
check("a raw References header string is parsed into its ids", msg["References"], OURS + " " + THEIRS)

# A half-parsed header must never reach the References line -- migration 017
# enforces the same shape on inbound_messages.thread_ids with a CHECK.
msg, _ = srv.build_message(req(references=["<a@b.com>", "notanid", "local-only", "<a@b.com>"]))
check("only well-formed ids are kept, and duplicates dropped", msg["References"], "<a@b.com>")
msg, _ = srv.build_message(req(references=["garbage"], in_reply_to="garbage"))
check("no usable id at all -> the headers are omitted, never guessed",
      (msg.get("In-Reply-To"), msg.get("References")), (None, None))
check("_ids is forgiving about the container, strict about the token",
      (srv._ids(None), srv._ids(""), srv._ids("<a@b.com>"), srv._ids(["<a@b.com>"])),
      ([], [], ["<a@b.com>"], ["<a@b.com>"]))

# --- what it refuses before opening a socket ---------------------------------

check("a From that is not this mailbox is refused -- this service holds the password",
      srv.build_message(req(**{"from": "someone@elsewhere.example"}))[1],
      "from address 'someone@elsewhere.example' is not this mailbox")
check("... the mailbox itself, with or without a display name, is fine",
      (srv.build_message(req(**{"from": MAILBOX}))[1],
       srv.build_message(req(**{"from": "Abdullah <%s>" % MAILBOX}))[1]), (None, None))
check("two recipients in one request are refused", srv.build_message(req(to="a@b.com, c@d.com"))[1],
      "not one bare recipient address: 'a@b.com, c@d.com'")
check("a display-name recipient is refused", srv.build_message(req(to="A <a@b.com>"))[1],
      "not one bare recipient address: 'A <a@b.com>'")
check("no subject is refused", srv.build_message(req(subject=" "))[1], "no subject")
check("no body is refused", srv.build_message(req(text=""))[1], "no plain-text body")
check("a non-string body is refused", srv.build_message(req(text={"html": "x"}))[1], "no plain-text body")

res = srv.send(req(to="nonsense"))
check("send() answers in nodemailer's shape even when it refuses",
      sorted(res.keys()), ["accepted", "error", "messageId", "rejected", "response"])
check("... with nothing accepted and the reason in `error`",
      (res["accepted"], res["rejected"], res["messageId"], res["error"].startswith("refused before sending:")),
      ([], [], None, True))

# The service was imported with no SMTP host configured, which is the honest
# shape of a misconfigured container: it must say so rather than connect.
res = srv.send(req())
check("no SMTP host configured -> refused before sending, nothing accepted",
      (res["accepted"], res["messageId"],
       "SMTP host, mailbox or password is not configured" in res["error"]), ([], None, True))

# --- the HTTP contract -------------------------------------------------------

check("the port the compose service and the workflow both use", srv.PORT, 8766)
check("one request at a time, so two ticks cannot interleave one SMTP session",
      type(srv._lock).__name__, "lock")
check("a body larger than MAX_BODY is refused, not read", srv.MAX_BODY, 256 * 1024)

print("Reply sender (smtp_send_server.py)\n")
for label in PASSED:
    print("  ok   " + label)
for label, why in FAILED:
    print("  FAIL " + label + "\n         " + why)
print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
