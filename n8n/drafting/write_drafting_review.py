"""Write DRAFTING_REVIEW.md from what is DEPLOYED, not from the repo.

    python n8n/drafting/write_drafting_review.py [execution_id]

Reads, read-only:
  * the n8n database -- workflow `drafting0001` as n8n holds it: the system
    prompt and the Anthropic request baked into Assess Grounding, every rule
    constant and tag in Assemble Drafts, the Claude node's settings;
  * one real execution of it (the latest, or the id given) -- the per-lead
    prompt exactly as it was sent;
  * the novascout database -- the live claims_library and its CHECKs.

Every tag the deployed Assemble Drafts can put on a draft must have a
description in TAG_DOCS below, or this refuses to write: an undocumented rule
in a review document is the same failure as an untested one.

Writes DRAFTING_REVIEW.md at the repo root.
"""
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(REPO, "DRAFTING_REVIEW.md")
PG = "nova-scout-postgres-1"
WF = "drafting0001"


def psql(db, sql):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", db, "-q", "-At", "-v", "ON_ERROR_STOP=1"],
                       input="SET default_transaction_read_only = on;\n" + sql, capture_output=True, encoding="utf-8")
    if p.returncode:
        sys.exit("psql failed: " + p.stderr)
    return p.stdout.strip()


def unflat(text):
    """n8n stores execution data with `flatted`: a JSON array whose first entry
    is the root and in which every string, object and array is referenced by
    its index, as a string."""
    arr = json.loads(text)
    memo = {}

    def rev(i):
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, dict):
            out = {}
            memo[i] = out
            for k, x in v.items():
                out[k] = rev(int(x)) if isinstance(x, str) else x
            return out
        if isinstance(v, list):
            out = []
            memo[i] = out
            for x in v:
                out.append(rev(int(x)) if isinstance(x, str) else x)
            return out
        memo[i] = v
        return v
    return rev(0)


# ---- deployed workflow ---------------------------------------------------------------------------------
row = json.loads(psql("n8n", "SELECT row_to_json(t) FROM (SELECT \"versionId\", active, \"activeVersionId\", \"updatedAt\", nodes "
                              "FROM workflow_entity WHERE id = '%s') t;" % WF))
nodes = {n["name"]: n for n in row["nodes"]}
assess = nodes["Assess Grounding"]["parameters"]["jsCode"]
assemble = nodes["Assemble Drafts"]["parameters"]["jsCode"]
claude = nodes["Claude Draft"]

SYSTEM_PROMPT = json.loads(re.search(r"^const SYSTEM_PROMPT = (\".*\");$", assess, re.M).group(1))
REQUEST = json.loads(re.search(r"^const CLAUDE_REQUEST = (\{.*?\n  \});$", assess, re.M | re.S).group(1))


def js_const(name, src=assemble):
    m = re.search(r"^const %s = (.+?);\n" % re.escape(name), src, re.M | re.S)
    if not m:
        sys.exit("const %s not found in the deployed Assemble Drafts" % name)
    return m.group(1).strip()


def js_list(name, src=assemble):
    return re.findall(r"'((?:[^'\\]|\\.)*)'", js_const(name, src))


TAGS = []
for t in re.findall(r"flags\.push\('([a-z-]+)'\)", assemble):
    if t not in TAGS:
        TAGS.append(t)

# ---- what each deployed tag means (plain English; the source is quoted below) --------------------------
TAG_DOCS = {
    "ask-none": ("Exactly one ask", "the ask field is empty or holds no question mark"),
    "ask-multiple": ("Exactly one ask", "the ask holds more than one question, or the body makes a request "
                     "(REQUEST_IN_BODY: \"would you\", \"let me know\", \"worth a look\", ...)"),
    "ask-scheduling": ("Ask: no scheduling link, no call length", "the ask names a call length, a schedule or a "
                       "booking slot (ASK_SCHEDULING)"),
    "product-name-repeat": ("Product name at most once", "\"Nova\" appears more than PRODUCT_NAME_MAX times in the message"),
    "product-name-unbracketed": ("Product name only in brackets", "\"Nova\" survives once every bracketed segment is removed"),
    "subject-product": ("Product name never in the subject", "the subject says \"Nova\""),
    "ai-repeat": ("\"AI\" at most once", "\"AI\" appears more than AI_MAX times in the message"),
    "ai-powered": ("Never \"AI-powered\"", "\"AI-powered\", \"AI-driven\" or \"AI-based\""),
    "subject-ai": ("\"AI\" never in the subject", "the subject says \"AI\""),
    "prospect-sponsor": ("No \"You're sponsoring\" hook", "the subject or body calls the prospect a sponsor (PROSPECT_SPONSOR)"),
    "claim-guarantee": ("Forbidden: guarantees", "GUARANTEE: \"guarantee\", \"never lose/miss\", \"no inquiry is lost\", "
                        "\"100%\", \"will win/double ...\", \"you'll never/always\""),
    "claim-number": ("Forbidden: numbers not in the library", "a digit token, a spelled-out number or multiplier "
                     "(NUMBER_WORDS) or a % that neither the lead's facts nor its offered claims hold; a digit in the "
                     "facts licenses its word; the fact sheet's own line numbers license nothing"),
    "claim-visitor-id": ("Forbidden: visitor identification", "VISITOR_ID: identifying, revealing or tracking visitors, "
                         "\"see who visits\""),
    "claim-named-tool": ("Forbidden: named CRMs and tools", "any name in NAMED_TOOLS (generic \"your CRM\" is fine)"),
    "claim-language": ("Forbidden: supported languages", "LANGUAGES: \"multilingual\", \"language(s)\", a named language"),
    "claim-chatbot": ("Never a chatbot", "\"chatbot\" or \"Q&A bot\""),
    "proof-missing": ("Proof: the geography-matched deployment", "no deployment from this lead's proof line is named "
                      "(or, for PR-BOTH, no \"two CROs\")"),
    "proof-geo": ("Proof: no other deployment", "a deployment this lead's proof line does not name"),
    "ungrounded-area": ("Prospect facts only from enrichment", "a therapeutic area the lead does not have is named"),
    "hook-opener": ("Never open on the founder's name or the city", "the first sentence starts with either"),
    "hook-source": ("A hook is about them, not the source", "opens \"ClinicalTrials.gov ...\"/\"According to\", or "
                    "credits a trial to their website"),
    "urls": ("Section 5: at most one plain URL", "more than MAX_URLS URLs"),
    "link-in-warmup": ("Existing link policy", "any URL in warm-up weeks 1-LINK_FREE_WEEKS"),
    "unapproved-link": ("Existing link policy", "any URL that is not the library's link line (so any URL the model "
                        "wrote, and any URL in the LinkedIn DM)"),
    "linkedin-link": ("Existing link policy", "a linkedin.com URL"),
    "pdf-link": ("Existing link policy", "a .pdf URL"),
    "subject": ("Subject length", "fewer than SUBJECT_MIN_CHARS or more than SUBJECT_MAX_CHARS characters"),
    "subject-reply": ("Subject: no \"Re:\", no \"Fwd:\"", "SUBJECT_REPLY_PREFIXES"),
    "subject-caps": ("Subject: no capitals", "a capitalised word after the first that the facts do not spell that "
                     "way and that is not CRO/CROs/BD, or an all-caps first word"),
    "subject-ungrounded": ("Subject from a prospect fact", "a digit the facts (or the confirmed headcount) do not "
                           "hold, or an area the lead lacks"),
    "empty": ("A message has a body", "the body is empty"),
    "long": ("Length", "body + ask over BODY_CEILING words (target BODY_TARGET_MIN-BODY_TARGET_MAX; shorter is fine)"),
    "adjective": ("No banned adjectives", "any word in BANNED_ADJECTIVES"),
    "merge-tag": ("No merge-tag tells", "[First Name], {{company}}, <domain>"),
    "claim-code": ("Claims only from the approved list", "the message reported a claim code this lead was not "
                   "offered, or reported none"),
    "unconfirmed-claim": ("Every claim confirmed by a human", "the message used a claims_library line with confirmed=false"),
}
undocumented = [t for t in TAGS if t not in TAG_DOCS]
stale = [t for t in TAG_DOCS if t not in TAGS]
if undocumented or stale:
    sys.exit("TAG_DOCS is out of step with the deployed Assemble Drafts -- undocumented: %r, no longer deployed: %r"
             % (undocumented, stale))

# ---- one real execution: the per-lead prompt as sent ---------------------------------------------------
exec_id = sys.argv[1] if len(sys.argv) > 1 else psql(
    "n8n", "SELECT id FROM execution_entity WHERE \"workflowId\" = '%s' AND status = 'success' ORDER BY id DESC LIMIT 1;" % WF)
ex = json.loads(psql("n8n", "SELECT row_to_json(t) FROM (SELECT e.id, e.mode, e.\"startedAt\", e.\"workflowVersionId\", d.data "
                            "FROM execution_entity e JOIN execution_data d ON d.\"executionId\" = e.id WHERE e.id = %s) t;"
                     % int(exec_id)))
run = unflat(ex["data"])["resultData"]["runData"]
sent = [it["json"] for r in run["Assess Grounding"] for br in r["data"]["main"] for it in (br or []) if it["json"].get("request")]
sample = sent[-1]
used = [it["json"] for r in run["Assemble Drafts"] for br in r["data"]["main"] for it in (br or [])]

# ---- live claims library ------------------------------------------------------------------------------
lib = json.loads(psql("novascout", "SELECT json_agg(t ORDER BY array_position(ARRAY['description','angle','benefit','proof','ask','link'], t.slot), t.code) "
                                   "FROM (SELECT code, slot, body, countries, measured, active, confirmed, note, updated_at FROM claims_library) t;"))
checks = psql("novascout", "SELECT conname || ' | ' || pg_get_constraintdef(oid) FROM pg_constraint "
                           "WHERE conrelid = 'claims_library'::regclass AND contype = 'c' ORDER BY conname;").splitlines()


# ---- write ---------------------------------------------------------------------------------------------
def fence(text, lang=""):
    return "```%s\n%s\n```" % (lang, text)


def md_cell(v):
    return ("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ")


sha = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
L = []
L.append("# Drafting review — Workflow 4, skill v3, as deployed")
L.append("")
L.append("Generated %s by `n8n/drafting/write_drafting_review.py`, read-only, from the live n8n and novascout "
         "databases — not from the repo." % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
L.append("")
L.append("| Source | Value |")
L.append("|---|---|")
L.append("| n8n workflow | `%s`, versionId `%s`, updated %s |" % (WF, row["versionId"], row["updatedAt"]))
L.append("| Published | %s |" % ("yes, version `%s`" % row["activeVersionId"] if row["active"] else
                                "**no** — publish it in the n8n UI; this document describes the saved version"))
L.append("| Assess Grounding code | sha256 `%s…` |" % sha(assess))
L.append("| Assemble Drafts code | sha256 `%s…` |" % sha(assemble))
L.append("| Sample execution | #%s (%s, %s, workflow version `%s`) |" % (ex["id"], ex["mode"], ex["startedAt"], ex["workflowVersionId"]))
L.append("| claims_library | %d rows, %d active, %d confirmed |" % (len(lib), sum(r["active"] for r in lib), sum(r["confirmed"] for r in lib)))
L.append("")
L.append("A human approves every draft before it can send. Nothing below changes that gate.")
L.append("")

L.append("## 1. The model call")
L.append("")
L.append("Node **%s**: `%s %s`, credential `%s` (the API key lives encrypted in n8n), on error: `%s`, "
         "retry: %s × %s ms apart. The body is the per-lead `$json.request` that Assess Grounding builds from this fixed part:"
         % (claude["name"], claude["parameters"]["method"], claude["parameters"]["url"],
            claude["credentials"]["anthropicApi"]["id"], claude.get("onError"), claude.get("maxTries"), claude.get("waitBetweenTries")))
L.append("")
L.append(fence(json.dumps(REQUEST, indent=2, ensure_ascii=False), "json"))
L.append("")
L.append("Plus, per lead, `system` (§2) and one user message (§3). Nothing else is sent: no `temperature`, `top_p` or "
         "`top_k` (a non-default value is a 400 on this model), no `thinking` (so the model's default, adaptive, applies), "
         "no prompt caching.")
L.append("")

L.append("## 2. The system prompt, exactly")
L.append("")
L.append(fence(SYSTEM_PROMPT))
L.append("")

L.append("## 3. The per-lead prompt — a real one, as sent")
L.append("")
L.append("Built per lead by Assess Grounding: the numbered facts (each with what it does not establish), which fact "
         "opens each message, what the subject is built from, and the approved claims for this lead — every active "
         "description, angle, benefit and ask, and only the proof line matched to its country. This is lead %s (%s, %s) "
         "from execution #%s:" % (sample["lead_id"], sample["company_name"], sample["country"], ex["id"]))
L.append("")
L.append(fence(sample["request"]["messages"][0]["content"]))
L.append("")

L.append("## 4. Every rule enforced in code")
L.append("")
L.append("### 4a. Before the model is called (Assess Grounding)")
L.append("")
for line in [
    "**Grounding guard (Master Ref §9, LOCKED):** fewer than %s of the four fact kinds (therapeutic area, named trial, "
    "city, founder name) → no model call; a `low-context` note is written instead." % js_const("MIN_FACTS", assess),
    "Only therapeutic areas in the locked taxonomy count, `Other` never does, and at most two reach the prompt.",
    "Country and `site_quality_notes` are never facts.",
    "Founder name and city never open a message; which fact opens each channel rotates on `lead_id`.",
    "The trial fact says \"registered under your company\" — never \"sponsor\" — and a trial-led hook is told to say "
    "\"Your recruiting trial …\" / \"You're running …\".",
    "A groundable lead with no active line for one of %s, or no proof line for its country and no fallback, is held at "
    "`contact_found` — no call, no draft." % ", ".join("`%s`" % s for s in js_list("LIBRARY_SLOTS", assess)),
    "ClinicalTrials.gov not answering → the lead is held, not drafted one fact short.",
]:
    L.append("- " + line)
L.append("")
L.append("### 4b. After the model answers (Assemble Drafts)")
L.append("")
L.append("A failed call writes **nothing** and the lead stays queued: an HTTP error, `stop_reason` other than `end_turn` "
         "(a refusal, a `max_tokens` cut-off), text that is not JSON, or JSON missing any of %s or the lists %s. "
         "There is no fallback model."
         % (", ".join("`%s`" % f for f in js_list("FIELDS")), ", ".join("`%s`" % f for f in js_list("CLAIM_LISTS"))))
L.append("")
L.append("Assembled, never generated: the greeting; the opt-out, verbatim — `%s`; the signature; and, from warm-up week "
         "%d on only, the library's link line (email only). The body that the length rule counts is everything between "
         "the greeting and the opt-out." % (json.loads(js_const("OPT_OUT")), int(js_const("LINK_FREE_WEEKS")) + 1))
L.append("")
L.append("A violation **tags** the draft's `variant` (`addressing/claim codes+tag,tag`); the draft is still written for the "
         "reviewer to see. Every tag the deployed node can emit:")
L.append("")
L.append("| Tag | Rule | Fires when |")
L.append("|---|---|---|")
for t in TAGS:
    L.append("| `%s` | %s | %s |" % (t, md_cell(TAG_DOCS[t][0]), md_cell(TAG_DOCS[t][1])))
L.append("")
L.append("The constants and patterns those rows name, as deployed:")
L.append("")
consts = ["BODY_TARGET_MIN", "BODY_TARGET_MAX", "BODY_CEILING", "MAX_URLS", "LINK_FREE_WEEKS", "SUBJECT_MIN_CHARS",
          "SUBJECT_MAX_CHARS", "SUBJECT_REPLY_PREFIXES", "PRODUCT_NAME", "PRODUCT_NAME_MAX", "AI_MAX", "BANNED_ADJECTIVES",
          "DEPLOYMENTS", "NAMED_TOOLS", "NUMBER_WORDS", "PROSPECT_SPONSOR", "GUARANTEE", "VISITOR_ID", "LANGUAGES",
          "CHATBOT", "AI_POWERED", "REQUEST_IN_BODY", "ASK_SCHEDULING", "HOOK_SOURCE_OPENING", "SUBJECT_ACRONYMS"]
L.append(fence("\n".join("%s = %s" % (c, re.sub(r"\s*\n\s*", " ", js_const(c))) for c in consts), "js"))
L.append("")
L.append("Attribution: each message's `variant` records the claim codes **that message** reported, in slot order. When "
         "it reports no ask (or no proof), code attributes one from the text — the ask line sharing at least half its "
         "words, the proof line whose deployment it names — rather than trusting the model to copy an identifier.")
L.append("")
L.append("### 4c. In the database")
L.append("")
L.append("`claims_library` refuses a line that breaks these, for every writer (NocoDB, psql, a migration):")
L.append("")
L.append(fence("\n".join(checks), "sql"))
L.append("")
L.append("`drafts` (migration 006): status must be pending/approved/rejected/sent; a rejection needs one of four reasons.")
L.append("")

L.append("## 5. The live claims library — all %d rows" % len(lib))
L.append("")
L.append("Only **active** rows reach the model; every row is `confirmed=false` until a human confirms it, and every "
         "draft built from an unconfirmed line is tagged `unconfirmed-claim`. Read the `note` before confirming: it "
         "records what the Nova Agent Kit code showed.")
L.append("")
L.append("| Code | Slot | Active | Confirmed | Countries | Body | Note |")
L.append("|---|---|---|---|---|---|---|")
for r in lib:
    L.append("| `%s` | %s | %s | %s | %s | %s | %s |" % (
        r["code"], r["slot"], "yes" if r["active"] else "no", "yes" if r["confirmed"] else "no",
        md_cell(", ".join(r["countries"]) if r["countries"] else "—"), md_cell(r["body"] or "*(empty)*"), md_cell(r["note"] or "")))
L.append("")

L.append("## 6. What the sample execution produced")
L.append("")
L.append("| Lead | Email words | DM words | Email claims | DM claims | Email tags | DM tags | Input tok | Output tok (thinking) |")
L.append("|---|---|---|---|---|---|---|---|---|")
for a in used:
    u = a.get("usage") or {}
    L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s (%s) |" % (
        a["lead_id"], a.get("email_words"), a.get("linkedin_words"), ".".join(a.get("email_claims") or []),
        ".".join(a.get("linkedin_claims") or []), ", ".join(a.get("email_flags") or []) or "—",
        ", ".join(a.get("linkedin_flags") or []) or "—", u.get("input_tokens"), u.get("output_tokens"),
        (u.get("output_tokens_details") or {}).get("thinking_tokens")))
tin = sum((a.get("usage") or {}).get("input_tokens") or 0 for a in used)
tout = sum((a.get("usage") or {}).get("output_tokens") or 0 for a in used)
L.append("")
L.append("API cost of that execution at $2 / $10 per MTok: **$%.4f** for %d leads (%d input + %d output tokens)."
         % ((tin * 2 + tout * 10) / 1e6, len(used), tin, tout))
L.append("")

with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(L) + "\n")
print("wrote %s (%d lines) from %s version %s and execution #%s; %d tags documented; %d library rows"
      % (OUT, len(L), WF, row["versionId"], ex["id"], len(TAGS), len(lib)))
