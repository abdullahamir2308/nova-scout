"""Write DRAFTING_REVIEW.md from what is DEPLOYED, not from the repo.

    python n8n/drafting/write_drafting_review.py [execution_id]

Reads, read-only:
  * the n8n database -- workflow `drafting0001` as n8n holds it: the system
    prompt and the Anthropic request baked into Assess Grounding, every rule
    constant and tag in Assemble Drafts, the Claude node's settings;
  * one real execution of it (the latest, or the id given) -- the per-lead
    prompt exactly as it was sent;
  * workflow `followup0001` (Workflow 6's follow-ups, composed by the same
    model under the same claim rules since 2026-10-02, skill section 8) and its
    latest execution that called the model;
  * the novascout database -- the live claims_library and its CHECKs.
  * the deployed Approval Gate and Apply Claim Check (auto-approval, migration
    014) in both workflows, and the live settings.auto_approve_email -- section 8.

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
    "claim-books": ("Forbidden: saying it books calls (settled 2026-10-02)", "NOVA_BOOKS: \"books the call\", \"into "
                    "your calendar\", \"schedules the call\" -- Nova sends the booking link and the sponsor books"),
    "claim-sop": ("Forbidden: SOPs or client documents as its source (settled 2026-10-02)", "SOP_SOURCE: \"SOPs\", "
                  "\"standard operating procedures\", \"your documents/documentation\" -- it answers from their website"),
    "claim-repeat": ("No repeats: the description and benefits never share a capability", "two claim codes the message "
                     "used share a capability (claims_library.capabilities, migration 013)"),
    "no-address": ("An email needs an address (2026-10-06)", "the contact has no email -- a LinkedIn-only contact "
                   "from Workflow 3b -- so the email goes nowhere; held, never auto-approved (email only)"),
}
undocumented = [t for t in TAGS if t not in TAG_DOCS]
stale = [t for t in TAG_DOCS if t not in TAGS]
if undocumented or stale:
    sys.exit("TAG_DOCS is out of step with the deployed Assemble Drafts -- undocumented: %r, no longer deployed: %r"
             % (undocumented, stale))

# ---- one real execution: the per-lead prompt as sent ---------------------------------------------------
# The latest that actually called the model: a published Drafting ticks every
# 30 minutes, and a tick with an empty queue runs nothing past Get Draft Batch.
def _execution(eid):
    e = json.loads(psql("n8n", "SELECT row_to_json(t) FROM (SELECT e.id, e.mode, e.\"startedAt\", e.\"workflowVersionId\", d.data "
                               "FROM execution_entity e JOIN execution_data d ON d.\"executionId\" = e.id WHERE e.id = %d) t;"
                        % int(eid)))
    return e, unflat(e["data"])["resultData"]["runData"]


if len(sys.argv) > 1:
    ex, run = _execution(sys.argv[1])
else:
    ex = None
    for _eid in psql("n8n", "SELECT id FROM execution_entity WHERE \"workflowId\" = '%s' AND status = 'success' "
                            "ORDER BY id DESC LIMIT 400;" % WF).split():
        _e, _run = _execution(_eid)
        if "Claude Draft" in _run:
            ex, run = _e, _run
            break
    if ex is None:
        sys.exit("no execution of %s in the last 400 called the model -- pass an execution id" % WF)
sent = [it["json"] for r in run["Assess Grounding"] for br in r["data"]["main"] for it in (br or []) if it["json"].get("request")]
sample = sent[-1]
used = [it["json"] for r in run["Assemble Drafts"] for br in r["data"]["main"] for it in (br or [])]

# ---- live claims library ------------------------------------------------------------------------------
lib = json.loads(psql("novascout", "SELECT json_agg(t ORDER BY array_position(ARRAY['description','angle','benefit','proof','ask','link'], t.slot), t.code) "
                                   "FROM (SELECT code, slot, body, countries, measured, active, confirmed, capabilities, note, updated_at FROM claims_library) t;"))
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
L.append("**Auto-approval (migration 014):** an email draft that passes every rule below, uses only confirmed claims and "
         "passes a second Sonnet 5.5 claim check is approved by the workflow that wrote it; anything else, and every "
         "LinkedIn DM, waits for a human with its `hold_reason`. Section 8 has the gate and the check as deployed.")
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
          "CHATBOT", "AI_POWERED", "NOVA_BOOKS", "SOP_SOURCE", "REQUEST_IN_BODY", "ASK_SCHEDULING", "HOOK_SOURCE_OPENING",
          "SUBJECT_ACRONYMS"]
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
L.append("`drafts` (migration 006): status must be pending/approved/rejected/sent; a rejection needs one of four reasons. "
         "Migration 014's trigger turns an `approved_by = 'auto'` approval that breaks a section 8 rule into a hold.")
L.append("")

L.append("## 5. The live claims library — all %d rows" % len(lib))
L.append("")
L.append("Only **active** rows reach the model. A row is confirmed by a human; a draft built from an unconfirmed line is "
         "tagged `unconfirmed-claim` and never auto-approves, and editing a confirmed line's text un-confirms it "
         "(migration 014). Read the `note` before confirming: it records what the Nova Agent Kit code showed.")
L.append("")
L.append("`Capabilities` is what a description or benefit line asserts (migration 013). Two lines in one message that "
         "share one say the same thing twice: the prompt names every such pair, and Assemble Drafts tags a message that "
         "uses one (`claim-repeat`).")
L.append("")
L.append("| Code | Slot | Active | Confirmed | Countries | Capabilities | Body | Note |")
L.append("|---|---|---|---|---|---|---|---|")
for r in lib:
    L.append("| `%s` | %s | %s | %s | %s | %s | %s | %s |" % (
        r["code"], r["slot"], "yes" if r["active"] else "no", "yes" if r["confirmed"] else "no",
        md_cell(", ".join(r["countries"]) if r["countries"] else "—"),
        md_cell(", ".join(r["capabilities"]) if r.get("capabilities") else "—"),
        md_cell(r["body"] or "*(empty)*"), md_cell(r["note"] or "")))
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

# ---- 7. follow-ups (Workflow 6, skill section 8) ----------------------------------------------------------
FU = "followup0001"
fu_row = json.loads(psql("n8n", "SELECT row_to_json(t) FROM (SELECT \"versionId\", active, \"activeVersionId\", \"updatedAt\", nodes "
                                 "FROM workflow_entity WHERE id = '%s') t;" % FU))
fu_nodes = {n["name"]: n for n in fu_row["nodes"]}
fu_build = fu_nodes["Build Follow-Up"]["parameters"]["jsCode"]
fu_assemble = fu_nodes["Assemble Follow-Up"]["parameters"]["jsCode"]
fu_trigger = [n for n in fu_row["nodes"] if n["type"] == "n8n-nodes-base.scheduleTrigger"][0]
FU_SYSTEM = json.loads(re.search(r"^const SYSTEM_PROMPT = (\".*\");$", fu_build, re.M).group(1))
FU_REQUEST = json.loads(re.search(r"^const CLAUDE_REQUEST = (\{.*\});$", fu_build, re.M).group(1))
fu_body = fu_assemble[fu_assemble.index("// Assemble Follow-Up -- n8n Code node"):]
FU_TAGS = []
for t in re.findall(r"flags\.push\('([a-z-]+)'\)", fu_body):
    if t not in FU_TAGS:
        FU_TAGS.append(t)
FU_TAG_DOCS = {
    "short": "follow-up #1 under FOLLOW_UP_1_MIN words (note + ask)",
    "long": "follow-up #1 over FOLLOW_UP_1_MAX, or #2 over FOLLOW_UP_2_MAX words (note + ask)",
    "fu-no-new-claim": "#1 did not add exactly one angle or benefit from the lines it was offered",
    "fu-repeat": "#1's added claim is one the first email used (its codes) or made in other words (the model's first_email_covers)",
    "fu-new-claim": "#2, the final note, added an angle or benefit",
}
fu_undoc = [t for t in FU_TAGS if t not in FU_TAG_DOCS and t not in TAG_DOCS]
if fu_undoc:
    sys.exit("the deployed Assemble Follow-Up pushes undocumented tags: %r -- add them to FU_TAG_DOCS" % fu_undoc)
fu_ex = None
for (eid,) in [(int(x),) for x in psql("n8n", "SELECT id FROM execution_entity WHERE \"workflowId\" = '%s' AND status = 'success' "
                                               "ORDER BY id DESC LIMIT 20;" % FU).split()]:
    e = json.loads(psql("n8n", "SELECT row_to_json(t) FROM (SELECT e.id, e.mode, e.\"startedAt\", e.\"workflowVersionId\", d.data "
                               "FROM execution_entity e JOIN execution_data d ON d.\"executionId\" = e.id WHERE e.id = %d) t;" % eid))
    rd = unflat(e["data"])["resultData"]["runData"]
    if "Claude Follow-Up" in rd:
        fu_ex, fu_run = e, rd
        break
fu_rules_ok = fu_assemble.find(assemble[:assemble.index("// Node body")]) != -1

L.append("## 7. Follow-ups — Workflow 6, composed under the same rules")
L.append("")
L.append("Drafting skill v3 §8 (2026-10-02): a follow-up is no longer a template. Workflow `%s` (versionId `%s`, %s) "
         "asks the same model for it, with the same request parameters, and checks it with the same rule functions."
         % (FU, fu_row["versionId"], "published" if fu_row["active"] else "**not published** — publish it in the n8n UI"))
L.append("")
L.append("| | |")
L.append("|---|---|")
L.append("| Schedule | `%s` — %s |" % (fu_trigger["name"], json.dumps(fu_trigger["parameters"]["rule"]["interval"])))
L.append("| Due | no reply %s days after the last send or follow-up; at most two follow-ups, then the lead is marked lost |"
         % re.search(r'"name": "follow_up_days", "value": (\d+)', json.dumps(fu_nodes["Config"]["parameters"])).group(1))
L.append("| Lengths (note + ask) | #1 %s–%s words; #2, the short final note, at most %s |" % (
    js_const("FOLLOW_UP_1_MIN", fu_body), js_const("FOLLOW_UP_1_MAX", fu_body), js_const("FOLLOW_UP_2_MAX", fu_body)))
L.append("| Claim rules | Assemble Follow-Up embeds the deployed Assemble Drafts' rule section verbatim: **%s** |"
         % ("identical to drafting0001's" if fu_rules_ok else "DIFFERS from drafting0001's — rebuild and re-import both"))
L.append("| Assemble Follow-Up code | sha256 `%s…` |" % sha(fu_assemble))
L.append("")
L.append("What it is offered: the first email exactly as sent (no greeting, opt-out or signature), the asks, the "
         "country's proof line, and for #1 only the angles and benefits that email did not use — a benefit that shares a "
         "capability with one it used is withheld too. Nothing else about the prospect reaches the model. A first email "
         "written under an older claims list (leads 7, 91 and 104 were emailed under v2) cannot be read off its codes, so "
         "the model reports which approved lines it already made (`first_email_covers`) and the added line is checked "
         "against that.")
L.append("")
L.append("The fixed request part:")
L.append("")
L.append(fence(json.dumps(FU_REQUEST, indent=2, ensure_ascii=False), "json"))
L.append("")
L.append("The system prompt, exactly:")
L.append("")
L.append(fence(FU_SYSTEM))
L.append("")
L.append("Tags Assemble Follow-Up adds on top of the inherited ones (§4b: the ask, product-name, \"AI\", sponsor, claim, "
         "area, link, repeat and unconfirmed rules all apply; `proof-missing` does not — a follow-up need not prove "
         "anything again, but a deployment it names must be the lead's):")
L.append("")
L.append("| Tag | Fires when |")
L.append("|---|---|")
for t in FU_TAGS:
    L.append("| `%s` | %s |" % (t, md_cell(FU_TAG_DOCS.get(t) or TAG_DOCS[t][1])))
L.append("")
if fu_ex:
    fu_sent = [it["json"] for r in fu_run["Build Follow-Up"] for br in r["data"]["main"] for it in (br or []) if it["json"].get("request")]
    fu_used = [it["json"] for r in fu_run["Assemble Follow-Up"] for br in r["data"]["main"] for it in (br or [])]
    L.append("A real per-lead prompt, as sent — lead %s, follow-up #%s, from execution #%s (%s, %s, workflow version `%s`%s):"
             % (fu_sent[0]["lead_id"], fu_sent[0]["follow_up"], fu_ex["id"], fu_ex["mode"], fu_ex["startedAt"],
                fu_ex["workflowVersionId"], "" if fu_ex["workflowVersionId"] == fu_row["versionId"]
                else " — an earlier version than the one described above; the system prompt and rules above are the "
                     "deployed ones"))
    L.append("")
    L.append(fence(fu_sent[0]["request"]["messages"][0]["content"]))
    L.append("")
    L.append("| Lead | # | Words | Claims | First email covers (model) | Tags | Input tok | Output tok (thinking) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a in fu_used:
        u = a.get("usage") or {}
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s (%s) |" % (
            a["lead_id"], a.get("follow_up"), a.get("words"), ".".join(a.get("claims") or []),
            ", ".join(a.get("first_email_covers") or []) or "—", ", ".join(a.get("flags") or []) or "—",
            u.get("input_tokens"), u.get("output_tokens"), (u.get("output_tokens_details") or {}).get("thinking_tokens")))
    fin = sum((a.get("usage") or {}).get("input_tokens") or 0 for a in fu_used)
    fout = sum((a.get("usage") or {}).get("output_tokens") or 0 for a in fu_used)
    L.append("")
    L.append("API cost of that execution at $2 / $10 per MTok: **$%.4f** for %d follow-ups (%d input + %d output tokens)."
             % ((fin * 2 + fout * 10) / 1e6, len(fu_used), fin, fout))
else:
    L.append("*No execution of `%s` has called the model yet.*" % FU)
L.append("")

# ---- 8. auto-approval (migration 014) ---------------------------------------------------------------------
gate_src = nodes["Approval Gate"]["parameters"]["jsCode"]
apply_src = nodes["Apply Claim Check"]["parameters"]["jsCode"]
rules_src = gate_src[:gate_src.index("// Node body")]
probe = subprocess.run(["node", "-e", rules_src + "\nprocess.stdout.write(JSON.stringify({model: CHECK_MODEL, effort: "
                        "CHECK_EFFORT, max_tokens: CHECK_MAX_TOKENS, schema: CHECK_SCHEMA, system: CHECK_SYSTEM_PROMPT, "
                        "max_repairs: MAX_REPAIRS, repairable: REPAIRABLE, repair_schema: REPAIR_SCHEMA, "
                        "repair_system: REPAIR_SYSTEM_PROMPT}));"],
                       capture_output=True, encoding="utf-8")
if probe.returncode:
    sys.exit("could not evaluate the deployed Approval Gate's rules: " + probe.stderr)
CHECK = json.loads(probe.stdout)
check_node = nodes["Claude Claim Check"]
fu_same = (fu_nodes["Approval Gate"]["parameters"]["jsCode"] == gate_src
           and fu_nodes["Apply Claim Check"]["parameters"]["jsCode"] == apply_src
           and fu_nodes["Claude Claim Check"]["parameters"] == check_node["parameters"]
           and all(fu_nodes.get("Apply Repair %d" % k, {}).get("parameters", {}).get("jsCode")
                   == nodes.get("Apply Repair %d" % k, {}).get("parameters", {}).get("jsCode")
                   and "Apply Repair %d" % k in nodes for k in range(1, CHECK["max_repairs"] + 1)))
repair_rounds = sorted(n for n in nodes if n.startswith("Claude Repair "))
flag = psql("novascout", "SELECT coalesce((SELECT value FROM settings WHERE key = 'auto_approve_email'), '(no row -- on)');")
held = psql("novascout", "SELECT count(*) || ' pending, ' || count(*) FILTER (WHERE hold_reason IS NOT NULL) || ' with a reason' "
                         "FROM drafts WHERE status = 'pending';")
L.append("## 8. Auto-approval — the gate and the claim check, as deployed")
L.append("")
L.append("| | |")
L.append("|---|---|")
L.append("| settings.auto_approve_email | `%s` (live) |" % flag)
L.append("| Approval Gate code | sha256 `%s…` |" % sha(gate_src))
L.append("| Apply Claim Check code | sha256 `%s…` (the gate's rules section + the apply body) |" % sha(apply_src))
L.append("| Repair rounds | %d (`MAX_REPAIRS`), deployed as %s |" % (CHECK["max_repairs"], ", ".join(repair_rounds)))
L.append("| Follow-Ups (`%s`) | %s |" % (FU, "the same gate, check and repair nodes, identical code and request" if fu_same
                                         else "**DIFFERS** from drafting0001's -- rebuild and re-import both"))
L.append("| The exceptions queue now | %s |" % held)
L.append("")
L.append("An email draft is approved by the workflow (`approved_by = 'auto'`) only if all of these hold; anything else is "
         "written `pending` with `hold_reason`:")
L.append("")
for line in [
    "`settings.auto_approve_email` is on (`auto-approve-off`) -- off, no claim check is called at all;",
    "it is an email: a LinkedIn DM never auto-approves (`linkedin`, Section 6);",
    "it is not a low-context note (`low-context`);",
    "no rule tag from section 4b on its `variant` (`rule-tags: ...`);",
    "every claim code on its `variant` is an active, confirmed line (`unconfirmed-claim: ...`, `no-claims`);",
    "the claim check below finds every claim supported by a confirmed line and every prospect fact in the record, and "
    "its quoted sentences cover the whole email (`claim-check: ...`, `claim-check-incomplete: ...`); a failed call "
    "holds it (`claim-check-failed: ...`) -- no fallback model. A hold for %s statements is first **repaired**, up to "
    "%d times (below); \"held\" for a claim-check failure means it still failed after the repairs."
    % ("/".join(CHECK["repairable"]), CHECK["max_repairs"]),
]:
    L.append("- " + line)
L.append("")
L.append("Node **%s**: `%s %s`, credential `%s`, on error `%s`, retry %s × %s ms. The body is the per-draft "
         "`$json.check_request` the Approval Gate builds from this fixed part, plus the confirmed lines, the prospect "
         "record (the fact sheet above; for a follow-up, the enrichment record and the first email as context) and the "
         "generated text:" % (check_node["name"], check_node["parameters"]["method"], check_node["parameters"]["url"],
                              check_node["credentials"]["anthropicApi"]["id"], check_node.get("onError"),
                              check_node.get("maxTries"), check_node.get("waitBetweenTries")))
L.append("")
L.append(fence(json.dumps({"model": CHECK["model"], "max_tokens": CHECK["max_tokens"],
                           "output_config": {"effort": CHECK["effort"],
                                             "format": {"type": "json_schema", "schema": CHECK["schema"]}}},
                          indent=2, ensure_ascii=False), "json"))
L.append("")
L.append("The check's system prompt, exactly:")
L.append("")
L.append(fence(CHECK["system"]))
L.append("")
L.append("The model judges statements; code decides. Apply Claim Check approves only if every `claim` is `supported` "
         "and names a code that is a confirmed line, every `prospect` fact is `supported`, every `none` is `none`, the "
         "quoted sentences (a leading \"Subject:\" label dropped) account for every word of the email, and a first email "
         "has at least one claim. The verdicts are kept in `drafts.claim_check`.")
L.append("")
L.append("**The repair loop.** A hold that names %s statements goes back to the drafting model (`Claude Repair N`, "
         "same model, effort and limits as the check) with each flagged sentence and the checker's exact reason; "
         "`Apply Repair N` applies the rewrites by code, only to flagged sentences, and a second copy of Assemble "
         "Drafts, the Approval Gate and the claim check run on the result. At most %d repairs; every attempt's verdicts, "
         "rewrites and cost stay in `drafts.claim_check.attempts`, and a final hold lists every attempt's reasons. "
         "The repair's schema and system prompt, exactly:" % ("/".join(CHECK["repairable"]), CHECK["max_repairs"]))
L.append("")
L.append(fence(json.dumps(CHECK["repair_schema"], indent=2), "json"))
L.append("")
L.append(fence(CHECK["repair_system"]))
L.append("")

with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(L) + "\n")
print("wrote %s (%d lines) from %s version %s and execution #%s; %d tags documented; %d library rows; "
      "follow-ups from %s version %s%s"
      % (OUT, len(L), WF, row["versionId"], ex["id"], len(TAGS), len(lib), FU, fu_row["versionId"],
         (" and execution #%s" % fu_ex["id"]) if fu_ex else " (no model execution yet)"))
