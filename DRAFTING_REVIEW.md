# Drafting review — Workflow 4, skill v3, as deployed

Generated 2026-10-01 19:54 UTC by `n8n/drafting/write_drafting_review.py`, read-only, from the live n8n and novascout databases — not from the repo.

| Source | Value |
|---|---|
| n8n workflow | `drafting0001`, versionId `d5e3857e-6900-49f1-9d47-ed10a1161731`, updated 2026-10-01T19:49:52.773+00:00 |
| Published | **no** — publish it in the n8n UI; this document describes the saved version |
| Assess Grounding code | sha256 `b5ae18fbe613ad81…` |
| Assemble Drafts code | sha256 `09dae0c6a45e6ebc…` |
| Sample execution | #789 (cli, 2026-10-01T19:49:57.259+00:00, workflow version `d5e3857e-6900-49f1-9d47-ed10a1161731`) |
| claims_library | 23 rows, 20 active, 0 confirmed |

A human approves every draft before it can send. Nothing below changes that gate.

## 1. The model call

Node **Claude Draft**: `POST https://api.anthropic.com/v1/messages`, credential `novascoutAnthropic01` (the API key lives encrypted in n8n), on error: `continueRegularOutput`, retry: 2 × 5000 ms apart. The body is the per-lead `$json.request` that Assess Grounding builds from this fixed part:

```json
{
  "model": "claude-sonnet-5-5",
  "max_tokens": 16000,
  "output_config": {
    "effort": "high",
    "format": {
      "type": "json_schema",
      "schema": {
        "type": "object",
        "properties": {
          "email_subject": {
            "type": "string"
          },
          "email_body": {
            "type": "string"
          },
          "email_ask": {
            "type": "string"
          },
          "linkedin_body": {
            "type": "string"
          },
          "linkedin_ask": {
            "type": "string"
          },
          "email_claims": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "linkedin_claims": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "email_subject",
          "email_body",
          "email_ask",
          "linkedin_body",
          "linkedin_ask",
          "email_claims",
          "linkedin_claims"
        ],
        "additionalProperties": false
      }
    }
  }
}
```

Plus, per lead, `system` (§2) and one user message (§3). Nothing else is sent: no `temperature`, `top_p` or `top_k` (a non-default value is a 400 on this model), no `thinking` (so the model's default, adaptive, applies), no prompt caching.

## 2. The system prompt, exactly

```
You write one cold first-touch email and one LinkedIn DM to a small contract
research organisation (CRO), sent by Abdullah Amir. A person reviews both before
anything is sent.

Each message gives you two lists. VERIFIED FACTS are the only facts about the
prospect that exist. APPROVED CLAIMS are the only things you may say about what we
built, the problem it solves, and who uses it. You may rephrase a claim. You may
never widen what it means.

THE PROSPECT -- one sentence, one fact.
- Open each message from the fact you are told to open it with: exactly one
  prospect fact, stated, about them ("Your site lists ...", "Your recruiting
  trial ...").
- Never build a sentence out of two facts. If fact 2 gives you a trial and fact 3
  a city, "your trial in that city" is a claim neither fact makes. Each fact ends
  with a sentence saying what it does not establish. Obey it literally.
- Everything not in the facts does not exist: no headcount, no clients, no growth,
  no plans, no praise of their website, no trial you were not given.
- Never describe them as sponsoring anything. In these emails "sponsor" means the
  biotech, pharma, device or academic company that hires a CRO -- their client.
  Say "running", "recruiting" or "your trial".
- A trial is on ClinicalTrials.gov, not on their site: never write "your site
  lists" about a trial. Name one trial at most. Their site is where the
  therapeutic areas come from, and a list of areas is what they work in, not a
  list of trials.
- Never open on a person's name or a city. Never write the company's name.

THE EMAIL, in about this order:
  1. Hook -- the one prospect fact.
  2. Pain and stakes -- ONE angle from the approved list. Do not stack them.
  3. What it does -- a description from the list the first time you mention it,
     then one or two benefits. Not more.
  4. Proof -- the proof line you were given, matched to their country.
  5. Ask -- exactly one, answerable with one word. It goes in the ask field; the
     body before it asks for nothing.

LENGTH: the body plus the ask is 70-110 words and never more than 125. Shorter is
fine if nothing useful is lost. Say one thing well.

SUBJECT: 30-55 characters, sentence case, no capitals except a name the facts
spell that way. Build it from the fact the email opens with, or from the pain
when you are told to. Never the product name. Never "AI". No "Re:", no "Fwd:".

NAMING THE PRODUCT:
- Describe it first, using a description line. The name appears at most once, in
  brackets: "(we call it Nova)". Never "Nova" anywhere else.
- Never call it a chatbot or a Q&A bot. It qualifies the inquiry, captures the
  details, and passes the lead on.
- The word "AI" appears at most once, inside the description. Never "AI-powered".

CLAIMS -- forbidden, all of them:
- guarantees ("you'll never lose a sponsor")
- any number, percentage or multiplier that is not in the approved claims or the
  facts
- claiming to identify anonymous website visitors
- naming any CRM, tool or integration
- supported languages
- any count of clients beyond the two named deployments
The stakes line is about the industry, not about us: "a single sponsor inquiry
can be a multi-million-dollar study" is allowed; "we will win you millions" is not.

THE ASK: one question. No links, no scheduling link, no call length, no second
question.

EVERYWHERE: plain text. No links of any kind, no bullets, no placeholders, no
merge tags. No greeting and no sign-off -- both are added afterwards. Never these
words: revolutionary, cutting-edge, innovative, game-changing, seamless. No invented urgency, no flattery, no
exclamation marks.

THE LINKEDIN DM follows the same rules. It has no subject and may be shorter.

One example, for another company. Copy the shape and the restraint, never its
facts -- your facts are only the ones in your own numbered list.
  Subject: Oncology sponsor inquiries after hours
  Your site lists oncology and immunology. In those areas, a single sponsor inquiry can be a multi-million-dollar study, and sponsors often write outside your working hours, so that inquiry waits until morning while they contact the next CRO.

  We built an AI assistant for CRO websites that answers sponsors from your own service pages at any hour, collects their therapeutic area and study phase, and books qualified calls into your calendar (we call it Nova). It's live at NoblePath, an oncology CRO in Türkiye.

  Would a 48-hour demo built on your own material be worth a look? One word back is enough.

Return JSON: email_subject; email_body (everything before the ask, paragraphs
separated by a blank line); email_ask; linkedin_body; linkedin_ask; email_claims and
linkedin_claims (the code of every approved claim each message used).
```

## 3. The per-lead prompt — a real one, as sent

Built per lead by Assess Grounding: the numbered facts (each with what it does not establish), which fact opens each message, what the subject is built from, and the approved claims for this lead — every active description, angle, benefit and ask, and only the proof line matched to its country. This is lead 104 (Atlant Clinical, Turkey) from execution #789:

```
Company: Atlant Clinical
Country: Turkey

VERIFIED FACTS. These are the only facts about this company that exist.
Each one is numbered. Read the whole bullet, including the sentence saying
what it does NOT establish:
1. Therapeutic areas listed on their own website: Oncology and Cardiovascular. This says nothing about which area any particular trial belongs to.
2. ClinicalTrials.gov lists 1 recruiting trial registered under your company. The only one named is: "Safety and Efficacy of RPH-104 Used to Prevent Recurrent Fever Attacks in Adult Patients With Colchicine Resistant or Colchicine Intolerant Familial Mediterranean Fever". Nothing else about them is known -- not their phase, their therapeutic area, nor where they run.
3. The company is based in Istanbul. This says nothing about where any trial runs or where any staff sit.

Open the email from fact 1. Open the LinkedIn DM from fact 2.
Build the email subject from fact 1 too -- the same fact the email opens with.
A hook built from the trial fact says "Your recruiting trial ..." or "You're
running ..." -- about them, not about ClinicalTrials.gov, and never "sponsoring".

APPROVED CLAIMS FOR THIS EMAIL. Nothing about the product, the problem or the
proof may come from anywhere else. Rephrase freely; never widen what a line says.

DESCRIPTION -- introduce what we built with one of these, the first time you mention it:
  [D1] an AI assistant for your website that turns sponsor inquiries into qualified leads
  [D2] an AI intake assistant for your website that answers sponsors, qualifies them, and books the call

PAIN AND STAKES ANGLES -- build the email around ONE:
  [ANG-HOURS] Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.
  [ANG-SILENT] How many sponsors visit your site and leave without ever contacting you?
  [ANG-SPEED] Sponsors choosing a CRO notice how quickly you respond, and a small CRO can't staff a BD desk around the clock.
  [ANG-STAKES] A single sponsor inquiry can be a multi-million-dollar study.
  [ANG-TIME] Your BD time should go to qualified sponsors, not to sorting every inquiry that arrives.

BENEFITS -- use one or two:
  [BEN-247] It answers sponsors from your own SOPs and service pages, in real time, at any hour.
  [BEN-BOOK] Qualified sponsors book a call straight into your calendar.
  [BEN-BRIEF] It collects the therapeutic area and study phase before your first call.
  [BEN-CAPTURE] A sponsor who shares their details becomes a named lead: company, contact, therapeutic area and study phase.
  [BEN-DECK] It sends your capabilities deck the moment a sponsor asks for it.
  [BEN-FIT] It's configured around your services and your process, not a template.
  [BEN-ROUTE] Leads land where your team already works, not in another dashboard.
  [BEN-SEE] You can see every sponsor lead it captured, and any question it passed to your team.

PROOF -- use this; it is matched to their country:
  [PR-TR] It's live at NoblePath, an oncology CRO in Türkiye.

ASK -- end each message with ONE of these, rephrased if you like:
  [A1] Would a 48-hour demo built on your own material be worth a look? One word back is enough.
  [A2] Worth a 48-hour demo on your own material? Reply yes and I'll set it up.

Write the email subject, the email (body and ask) and the LinkedIn DM (body and ask).
Write no greeting and no sign-off -- those are added afterwards. In email_claims
and linkedin_claims, list the code of every claim that message used.
```

## 4. Every rule enforced in code

### 4a. Before the model is called (Assess Grounding)

- **Grounding guard (Master Ref §9, LOCKED):** fewer than 2 of the four fact kinds (therapeutic area, named trial, city, founder name) → no model call; a `low-context` note is written instead.
- Only therapeutic areas in the locked taxonomy count, `Other` never does, and at most two reach the prompt.
- Country and `site_quality_notes` are never facts.
- Founder name and city never open a message; which fact opens each channel rotates on `lead_id`.
- The trial fact says "registered under your company" — never "sponsor" — and a trial-led hook is told to say "Your recruiting trial …" / "You're running …".
- A groundable lead with no active line for one of `description`, `angle`, `benefit`, `proof`, `ask`, or no proof line for its country and no fallback, is held at `contact_found` — no call, no draft.
- ClinicalTrials.gov not answering → the lead is held, not drafted one fact short.

### 4b. After the model answers (Assemble Drafts)

A failed call writes **nothing** and the lead stays queued: an HTTP error, `stop_reason` other than `end_turn` (a refusal, a `max_tokens` cut-off), text that is not JSON, or JSON missing any of `email_subject`, `email_body`, `email_ask`, `linkedin_body`, `linkedin_ask` or the lists `email_claims`, `linkedin_claims`. There is no fallback model.

Assembled, never generated: the greeting; the opt-out, verbatim — `If this isn't relevant, reply 'no' and I won't follow up.`; the signature; and, from warm-up week 3 on only, the library's link line (email only). The body that the length rule counts is everything between the greeting and the opt-out.

A violation **tags** the draft's `variant` (`addressing/claim codes+tag,tag`); the draft is still written for the reviewer to see. Every tag the deployed node can emit:

| Tag | Rule | Fires when |
|---|---|---|
| `ask-none` | Exactly one ask | the ask field is empty or holds no question mark |
| `ask-multiple` | Exactly one ask | the ask holds more than one question, or the body makes a request (REQUEST_IN_BODY: "would you", "let me know", "worth a look", ...) |
| `ask-scheduling` | Ask: no scheduling link, no call length | the ask names a call length, a schedule or a booking slot (ASK_SCHEDULING) |
| `product-name-repeat` | Product name at most once | "Nova" appears more than PRODUCT_NAME_MAX times in the message |
| `product-name-unbracketed` | Product name only in brackets | "Nova" survives once every bracketed segment is removed |
| `ai-repeat` | "AI" at most once | "AI" appears more than AI_MAX times in the message |
| `ai-powered` | Never "AI-powered" | "AI-powered", "AI-driven" or "AI-based" |
| `claim-guarantee` | Forbidden: guarantees | GUARANTEE: "guarantee", "never lose/miss", "no inquiry is lost", "100%", "will win/double ...", "you'll never/always" |
| `claim-number` | Forbidden: numbers not in the library | a digit token, a spelled-out number or multiplier (NUMBER_WORDS) or a % that neither the lead's facts nor its offered claims hold; a digit in the facts licenses its word; the fact sheet's own line numbers license nothing |
| `claim-visitor-id` | Forbidden: visitor identification | VISITOR_ID: identifying, revealing or tracking visitors, "see who visits" |
| `claim-named-tool` | Forbidden: named CRMs and tools | any name in NAMED_TOOLS (generic "your CRM" is fine) |
| `claim-language` | Forbidden: supported languages | LANGUAGES: "multilingual", "language(s)", a named language |
| `claim-chatbot` | Never a chatbot | "chatbot" or "Q&A bot" |
| `proof-geo` | Proof: no other deployment | a deployment this lead's proof line does not name |
| `proof-missing` | Proof: the geography-matched deployment | no deployment from this lead's proof line is named (or, for PR-BOTH, no "two CROs") |
| `hook-opener` | Never open on the founder's name or the city | the first sentence starts with either |
| `hook-source` | A hook is about them, not the source | opens "ClinicalTrials.gov ..."/"According to", or credits a trial to their website |
| `urls` | Section 5: at most one plain URL | more than MAX_URLS URLs |
| `link-in-warmup` | Existing link policy | any URL in warm-up weeks 1-LINK_FREE_WEEKS |
| `unapproved-link` | Existing link policy | any URL that is not the library's link line (so any URL the model wrote, and any URL in the LinkedIn DM) |
| `linkedin-link` | Existing link policy | a linkedin.com URL |
| `pdf-link` | Existing link policy | a .pdf URL |
| `subject` | Subject length | fewer than SUBJECT_MIN_CHARS or more than SUBJECT_MAX_CHARS characters |
| `subject-product` | Product name never in the subject | the subject says "Nova" |
| `subject-ai` | "AI" never in the subject | the subject says "AI" |
| `subject-reply` | Subject: no "Re:", no "Fwd:" | SUBJECT_REPLY_PREFIXES |
| `subject-caps` | Subject: no capitals | a capitalised word after the first that the facts do not spell that way and that is not CRO/CROs/BD, or an all-caps first word |
| `subject-ungrounded` | Subject from a prospect fact | a digit the facts (or the confirmed headcount) do not hold, or an area the lead lacks |
| `empty` | A message has a body | the body is empty |
| `long` | Length | body + ask over BODY_CEILING words (target BODY_TARGET_MIN-BODY_TARGET_MAX; shorter is fine) |
| `adjective` | No banned adjectives | any word in BANNED_ADJECTIVES |
| `merge-tag` | No merge-tag tells | [First Name], {{company}}, <domain> |
| `prospect-sponsor` | No "You're sponsoring" hook | the subject or body calls the prospect a sponsor (PROSPECT_SPONSOR) |
| `ungrounded-area` | Prospect facts only from enrichment | a therapeutic area the lead does not have is named |
| `claim-code` | Claims only from the approved list | the message reported a claim code this lead was not offered, or reported none |
| `unconfirmed-claim` | Every claim confirmed by a human | the message used a claims_library line with confirmed=false |

The constants and patterns those rows name, as deployed:

```js
BODY_TARGET_MIN = 70
BODY_TARGET_MAX = 110
BODY_CEILING = 125
MAX_URLS = 1
LINK_FREE_WEEKS = 2
SUBJECT_MIN_CHARS = 30
SUBJECT_MAX_CHARS = 55
SUBJECT_REPLY_PREFIXES = ['re', 'fwd']
PRODUCT_NAME = 'Nova'
PRODUCT_NAME_MAX = 1
AI_MAX = 1
BANNED_ADJECTIVES = [ 'revolutionary', 'cutting-edge', 'cutting edge', 'innovative', 'game-changing', 'game changing', 'seamless', 'best-in-class', 'world-class', 'state-of-the-art', 'leverage', 'synergy', 'unlock', 'supercharge', ]
DEPLOYMENTS = ['NoblePath', 'Vertex']
NAMED_TOOLS = [ 'hubspot', 'salesforce', 'pipedrive', 'zoho', 'microsoft dynamics', 'dynamics 365', 'monday.com', 'freshsales', 'freshworks', 'zendesk', 'google sheets', 'google sheet', 'airtable', 'slack', 'microsoft teams', 'calendly', 'gmail', 'whatsapp', 'veeva', 'intercom', 'resend', 'mailchimp', ]
NUMBER_WORDS = [ 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'twenty', 'thirty', 'fifty', 'hundred', 'hundreds', 'thousand', 'thousands', 'million', 'millions', 'billion', 'dozen', 'dozens', 'double', 'doubles', 'doubled', 'triple', 'triples', 'tripled', 'twice', 'half', 'percent', 'tenfold', ]
PROSPECT_SPONSOR = [ /\byou(?:['’]re| are)\s+(?:also\s+|currently\s+|now\s+|actively\s+)?sponsor(?:ing|s)?\b/i, /\byou(?:['’]re| are)\s+(?:the|a|an)\s+(?:\w+\s+)?sponsor\b/i, /\byou\s+sponsor(?:ed|s)?\b/i, /\byou as (?:the |a )?sponsor\b/i, /\bsponsored by you\b/i, /\byour (?:own )?(?:sponsored|sponsorship)\b/i, ]
GUARANTEE = /\bguarantee\w*|\bnever (?:again )?(?:lose|miss)\b|\bno (?:lead|inquiry|inquiries|sponsor|sponsors) (?:is |are |gets |get |will be )?(?:ever )?(?:lost|missed)\b|\b100 ?%|\bwill (?:win|double|triple|increase|grow|boost)\b|\byou(?:['’]ll| will) (?:never|always|win|get more)\b|\b(?:ensures?|makes? sure) (?:you|that you) never\b/i
VISITOR_ID = /\b(?:identif\w*|reveal\w*|unmask\w*|de-?anonymi\w*|track\w*)\b[^.?!]{0,60}\b(?:visit\w*|visitors?|anonymous|traffic|browse\w*)\b|\banonymous (?:visitors?|traffic)\b|\b(?:see|know|tells? you|shows? you)\s+(?:exactly\s+)?(?:who|which \w+)\s+(?:visit\w*|browse\w*|land\w* on|came to)\b/i
LANGUAGES = /\bmulti-?lingual\b|\blanguages?\b|\b(?:turkish|spanish|arabic|german|french|portuguese|english|polish|romanian|hungarian|czech|hindi|urdu)\b/i
CHATBOT = /\bchat ?bots?\b|\bQ ?& ?A bots?\b/i
AI_POWERED = /\bAI[- ]?(?:powered|driven|based)\b/i
REQUEST_IN_BODY = /\b(?:would you|could you|are you open|worth a (?:look|chat|call|try|conversation)|let me know|reply (?:yes|no|with|to)|one word back|shall i|should i|want me to|happy to (?:set|send|show|share|walk)|interested in (?:a|seeing|trying|hearing)|open to a|up for a|free for a)\b/i
ASK_SCHEDULING = /\b\d+[- ]?min(?:ute)?s?\b|\b(?:fifteen|twenty|thirty)[- ]minutes?\b|\bcalendly\b|\bschedul\w*|\bbook (?:a |some )?(?:time|slot|call|meeting)\b|\b(?:time|slot)s? (?:that )?works?\b/i
HOOK_SOURCE_OPENING = /^\s*(clinicaltrials\.gov|according to)\b/i
SUBJECT_ACRONYMS = ['CRO', 'CROS', 'BD']
```

Attribution: each message's `variant` records the claim codes **that message** reported, in slot order. When it reports no ask (or no proof), code attributes one from the text — the ask line sharing at least half its words, the proof line whose deployment it names — rather than trusting the model to copy an identifier.

### 4c. In the database

`claims_library` refuses a line that breaks these, for every writer (NocoDB, psql, a migration):

```sql
claims_active_has_body | CHECK (((NOT active) OR (COALESCE(btrim(body), ''::text) <> ''::text)))
claims_ai_only_in_description | CHECK (((body IS NULL) OR ((slot = 'description'::text) AND (regexp_count(body, '\mAI\M'::text, 1, 'i'::text) <= 1)) OR ((slot <> 'description'::text) AND (body !~* '\mAI\M'::text))))
claims_ask_one_question | CHECK (((slot <> 'ask'::text) OR (body IS NULL) OR (regexp_count(body, '\?'::text) = 1)))
claims_countries_are_geographies | CHECK (((countries IS NULL) OR ((cardinality(countries) > 0) AND (countries <@ ARRAY['Turkey'::text, 'Mexico'::text, 'India'::text, 'Pakistan'::text, 'Egypt'::text, 'Poland'::text, 'Romania'::text, 'Hungary'::text, 'Czech Republic'::text, 'UAE'::text, 'South Africa'::text, 'Brazil'::text, 'Argentina'::text]))))
claims_countries_only_geo | CHECK (((countries IS NULL) OR (slot = ANY (ARRAY['proof'::text, 'link'::text]))))
claims_library_code_check | CHECK ((code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'::text))
claims_link_is_one_plain_url | CHECK (((slot <> 'link'::text) OR (body IS NULL) OR ((regexp_count(body, '(https?://|www\.)'::text, 1, 'i'::text) = 1) AND (body !~* 'linkedin\.com'::text) AND (body !~* '\.pdf\M'::text))))
claims_measured_only_proof | CHECK (((NOT measured) OR (slot = 'proof'::text)))
claims_no_ai_powered_no_chatbot | CHECK (((body IS NULL) OR (body !~* '(\mAI[- ]?powered\M|\mchat ?bots?\M|\mQ ?& ?A bot\M)'::text)))
claims_no_guarantee | CHECK (((body IS NULL) OR (body !~* '\mguarantee'::text)))
claims_no_product_name | CHECK (((body IS NULL) OR (body !~* '\mnova\M'::text)))
claims_no_url_outside_link | CHECK (((slot = 'link'::text) OR (body IS NULL) OR (body !~* '(https?://|www\.)'::text)))
claims_prospect_never_sponsor | CHECK (((body IS NULL) OR (body !~* '\myou(''re| are) sponsoring\M'::text)))
claims_slot_v3 | CHECK ((slot = ANY (ARRAY['description'::text, 'angle'::text, 'benefit'::text, 'proof'::text, 'ask'::text, 'link'::text])))
```

`drafts` (migration 006): status must be pending/approved/rejected/sent; a rejection needs one of four reasons.

## 5. The live claims library — all 23 rows

Only **active** rows reach the model; every row is `confirmed=false` until a human confirms it, and every draft built from an unconfirmed line is tagged `unconfirmed-claim`. Read the `note` before confirming: it records what the Nova Agent Kit code showed.

| Code | Slot | Active | Confirmed | Countries | Body | Note |
|---|---|---|---|---|---|---|
| `D1` | description | yes | no | — | an AI assistant for your website that turns sponsor inquiries into qualified leads |  |
| `D2` | description | yes | no | — | an AI intake assistant for your website that answers sponsors, qualifies them, and books the call | Code reading 2026-10-02 (not a [verify] line): Nova does not book calls itself. After capture_sponsor_lead succeeds it offers the booking link from CALENDLY_BOOKING_URL (lib/tenants/loader.ts), only if that is set. Confirm or narrow "books the call". |
| `ANG-HOURS` | angle | yes | no | — | Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO. |  |
| `ANG-SILENT` | angle | yes | no | — | How many sponsors visit your site and leave without ever contacting you? |  |
| `ANG-SPEED` | angle | yes | no | — | Sponsors choosing a CRO notice how quickly you respond, and a small CRO can't staff a BD desk around the clock. |  |
| `ANG-STAKES` | angle | yes | no | — | A single sponsor inquiry can be a multi-million-dollar study. | Industry fact, not a Nova result (skill section 4). |
| `ANG-TIME` | angle | yes | no | — | Your BD time should go to qualified sponsors, not to sorting every inquiry that arrives. | [verify] SUPPORTED 2026-10-02: Nova routes non-sponsor visitors to their own flows -- investigators and sites to capture_investigator_registration, trainees to capture_course_enrollment (lib/agent/tools/; system prompt "Distinguishing Sponsor Intent from Investigator Intent"). Seeded verbatim. |
| `BEN-247` | benefit | yes | no | — | It answers sponsors from your own SOPs and service pages, in real time, at any hour. | Code reading 2026-10-02 (not a [verify] line): the NoblePath knowledge base is the crawled website (tenants/noblepath/scripts/scraper.py); no SOP content is in it. The loader accepts any knowledge-base text, so SOPs are possible per tenant, but none is live. Confirm or narrow "SOPs". |
| `BEN-BOOK` | benefit | yes | no | — | Qualified sponsors book a call straight into your calendar. | Code reading 2026-10-02 (not a [verify] line): booking is a link (CALENDLY_BOOKING_URL) offered in the confirmation after a lead is captured, only when the variable is set. Confirm before use. |
| `BEN-BRIEF` | benefit | yes | no | — | It collects the therapeutic area and study phase before your first call. | [verify] NARROWED 2026-10-02. Was: "It collects the study brief before your first call." The RFP intake (capture_sponsor_lead) records therapeutic area and study phase, plus free-text notes; there are no fields for protocol, timelines, site count or budget, and pricing questions are escalated, not collected. |
| `BEN-CAPTURE` | benefit | yes | no | — | A sponsor who shares their details becomes a named lead: company, contact, therapeutic area and study phase. | [verify] NARROWED 2026-10-02. Was: "Every sponsor who engages becomes a named lead: company, contact, and what they're planning." capture_sponsor_lead requires company_name, therapeutic_area, study_phase, contact_name, contact_email (notes optional) and is only called once all five are confirmed; a visitor who declines is not captured, so "every sponsor who engages" was wider than the code. |
| `BEN-DECK` | benefit | yes | no | — | It sends your capabilities deck the moment a sponsor asks for it. | Code reading 2026-10-02: capture_capabilities_request emails the deck once the visitor gives an email address. Supported. |
| `BEN-FIT` | benefit | yes | no | — | It's configured around your services and your process, not a template. |  |
| `BEN-ROUTE` | benefit | yes | no | — | Leads land where your team already works, not in another dashboard. | Code reading 2026-10-02 (not a [verify] line): deliverLead() emails the team and pushes the lead to a CRM and a spreadsheet when configured (lib/integrations/). Nova also has its own leads dashboard (app/dashboard), so "not in another dashboard" means "not only". Confirm the wording. |
| `BEN-SEE` | benefit | yes | no | — | You can see every sponsor lead it captured, and any question it passed to your team. | [verify] NARROWED 2026-10-02. Was: "You can see which sponsors engaged and what they asked." The dashboard (app/dashboard) lists captured leads with their fields and notes, and escalations with the visitor's unanswered question. Conversations are not stored and analytics are anonymous counts, so a sponsor who engaged without leaving details, and what they asked, are not visible. |
| `PR-BOTH` | proof | yes | no | — | It's live at two CROs, in Türkiye and Mexico. | No countries: serves every lead no other proof line serves. |
| `PR-MX` | proof | yes | no | Mexico, Brazil, Argentina | It's live at Vertex Clinical Research in Mexico. | Mexico and Latin America. The Nova Agent Kit repo holds only the NoblePath tenant; the Vertex deployment is not visible in that source. |
| `PR-MX-N` | proof | no | no | Mexico, Brazil, Argentina | *(empty)* | Empty on purpose. Fill ONLY with a number measured from Nova's own dashboard at Vertex, then activate. Never from a web source. When active it replaces PR-MX. |
| `PR-TR` | proof | yes | no | Turkey, Egypt, UAE, Romania, Hungary, Poland, Czech Republic | It's live at NoblePath, an oncology CRO in Türkiye. | Turkiye and nearby. Which countries count as nearby is a judgement -- edit countries to change it. |
| `PR-TR-N` | proof | no | no | Turkey, Egypt, UAE, Romania, Hungary, Poland, Czech Republic | *(empty)* | Empty on purpose. Fill ONLY with a number measured from Nova's own dashboard at NoblePath, then activate. Never from a web source. When active it replaces PR-TR. |
| `A1` | ask | yes | no | — | Would a 48-hour demo built on your own material be worth a look? One word back is enough. |  |
| `A2` | ask | yes | no | — | Worth a 48-hour demo on your own material? Reply yes and I'll set it up. |  |
| `L-NP` | link | no | no | — | *(empty)* | The one plain URL allowed after warm-up week 2: NoblePath's site, as a full URL (https://...). Workflow 4 appends it after the composed body only from warm-up week 3 on. countries works as it does for proof. |

## 6. What the sample execution produced

| Lead | Email words | DM words | Email claims | DM claims | Email tags | DM tags | Input tok | Output tok (thinking) |
|---|---|---|---|---|---|---|---|---|
| 7 | 108 | 80 | D2.ANG-HOURS.BEN-247.BEN-BOOK.PR-MX.A1 | D2.ANG-HOURS.PR-MX.A2 | unconfirmed-claim | unconfirmed-claim | 3429 | 2453 (1942) |
| 50 | 100 | 85 | D2.ANG-HOURS.BEN-247.PR-BOTH.A1 | D2.ANG-HOURS.PR-BOTH.A2 | unconfirmed-claim | unconfirmed-claim | 3414 | 2399 (1904) |
| 91 | 104 | 78 | D2.ANG-HOURS.BEN-247.PR-TR.A1 | D2.ANG-HOURS.PR-TR.A2 | unconfirmed-claim | unconfirmed-claim | 3302 | 1675 (1203) |
| 104 | 107 | 70 | D2.ANG-HOURS.BEN-247.BEN-BOOK.PR-TR.A1 | D1.ANG-STAKES.BEN-BRIEF.PR-TR.A2 | unconfirmed-claim | unconfirmed-claim | 3460 | 2086 (1585) |

API cost of that execution at $2 / $10 per MTok: **$0.1133** for 4 leads (13605 input + 8613 output tokens).

