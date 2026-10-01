# Nova Scout — Drafting Skill v3
## Rules for the drafting model (Workflow 4) — first-touch email and LinkedIn DM

> Companion to NovaScout_MasterRef.md §9 Workflow 4.
> The live claims are in the `claims_library` table. §4 below is the seed it starts from — the table wins if they differ.
> A human approves every draft before it can send. That gate does not change.

---

## 0. Rules that cannot bend

1. **Prospect facts come only from the enrichment record.** No inference, no "likely", no joining two facts into a claim neither supports.
2. **Product claims come only from the approved claims list (§4).** The model may rephrase a claim. It may never widen what it means.
3. **No outcome numbers, guarantees, or results about Nova.** Neither live deployment has a measured result yet. The metric slots stay empty until a real number exists from Nova's own dashboard.

---

## 1. What changed in v3, and why

| Change | v2 | v3 | Why |
|---|---|---|---|
| Drafting model | local qwen3.5:9b | Claude Sonnet 5.5 (`claude-sonnet-5-5`), drafting node only | The 9B model needed verbatim line-picking to stay safe, and that made the copy stiff. A frontier model can compose natural copy and still follow the claim rules. Enrichment and scoring stay local. |
| How the email is built | model writes the hook; library lines pasted in verbatim | model composes the whole email from the hook fact plus approved claims | Natural flow. The claims list still limits what can be said. |
| Length | hard 80-word cap | target 70–110 words, ceiling 125 | Public 2026 benchmarks find 50–125 words gets the most replies. The cap moves; it does not disappear. |
| Product name | "Nova" stated as the product | describe what it does first, "(we call it Nova)" once | A prospect instantly understands "an AI assistant that turns sponsor inquiries into leads". A product name means nothing to them yet. |
| Claims | questions about after-hours inquiries | pain, stakes, and concrete benefits tied to real Nova features | More reasons to care, all true. |
| Terminology | "pharma team" for the client party | "sponsor" for the client party; the hook never calls the prospect a sponsor | "Sponsor" is the word CROs actually use, and it includes biotech, device and academic sponsors that "pharma team" leaves out. The v2 collision came from hooks saying "You're sponsoring…", so that phrasing is now banned instead. |

---

## 2. Email shape

The order can flex. These beats should all be present, in about this order:

| Beat | Content | Source |
|---|---|---|
| Subject | a prospect fact or the pain | enrichment or §4 angle |
| Hook | one fact about the prospect | enrichment only |
| Pain and stakes | one angle from §4 | §4 |
| What it does | the description, plus 1–2 benefits | §4 |
| Proof | one named live deployment, matched to the lead's geography | §4 |
| Ask | exactly one, answerable with one word | §4 |
| Opt-out and signature | fixed text | §5 of the master doc |

---

## 3. Rules for the model

**Length.** The body (everything above the opt-out line) should be 70–110 words and never more than 125. Shorter is fine if nothing useful is lost. Say one thing well.

**Subject.** 30–55 characters. Built from a prospect fact or the pain. Never the product name. Never "AI". No "Re:", no "Fwd:", no capitals.

**Hook.**
- Exactly one prospect fact.
- Make it about them: "Your site lists…", "Your recruiting trial…".
- Never describe the prospect as sponsoring anything ("You're sponsoring…" is banned). Use "running", "recruiting", or "your trial".
- Never open on the founder's name or the city.

**Pick one angle.** Choose one pain from §4 and build the email around it. Do not stack every benefit. Use one or two benefits at most.

**Naming the product.**
- The first mention is a description, for example "an AI assistant for your website that turns sponsor inquiries into qualified leads".
- The name appears at most once, in brackets: "(we call it Nova)".
- Never describe it as a chatbot or a Q&A bot. It qualifies the inquiry, captures the details, and books the call.
- The word "AI" appears at most once, inside the description. Never "AI-powered".

**Claims.**
- Rephrase claims from §4 freely. Never widen them.
- Forbidden:
  - guarantees ("you'll never lose a sponsor")
  - any number, percentage or multiplier that is not in §4
  - claiming to identify anonymous website visitors
  - naming a specific CRM, tool or integration
  - supported languages
  - any count of clients beyond the two named ones
- The stakes line is about the industry, not about Nova: "a single sponsor inquiry can be a multi-million-dollar study" is allowed; "Nova will win you millions" is not.

**Ask.** Exactly one ask. The reply costs one word. No links, no scheduling link, no "30-minute call".

**Everywhere.** Plain text. No banned adjectives (revolutionary, cutting-edge, innovative, game-changing, seamless). No invented urgency.

---

## 4. Approved claims — seed (live copy is `claims_library`)

Every line starts `confirmed = false` until a human confirms it. Lines marked **[verify]** must be checked against the Nova Agent Kit source before they go live; if the code doesn't support a line, drop it or narrow it.

### Description — how to introduce it (pick one)
- D1: an AI assistant for your website that turns sponsor inquiries into qualified leads
- D2: an AI intake assistant for your website that answers sponsors, qualifies them, and books the call

### Pain and stakes angles (pick one)
- ANG-HOURS: Sponsors often research CROs outside your working hours, frequently from another time zone. An inquiry sent at 11pm waits until morning, and by then they may have moved on to the next CRO.
- ANG-SILENT: How many sponsors visit your site and leave without ever contacting you?
- ANG-STAKES: A single sponsor inquiry can be a multi-million-dollar study. *(industry fact, not a Nova result)*
- ANG-SPEED: Sponsors choosing a CRO notice how quickly you respond, and a small CRO can't staff a BD desk around the clock.
- ANG-TIME: Your BD time should go to qualified sponsors, not to sorting every inquiry that arrives. **[verify: Nova routes non-sponsor visitors, e.g. investigators or trainees, to their own flows]**

### Benefits (pick one or two)
- BEN-247: It answers sponsors from your own SOPs and service pages, in real time, at any hour.
- BEN-CAPTURE: Every sponsor who engages becomes a named lead: company, contact, and what they're planning. **[verify fields captured]**
- BEN-BRIEF: It collects the study brief before your first call. **[verify: which study details the RFP intake captures]**
- BEN-BOOK: Qualified sponsors book a call straight into your calendar.
- BEN-ROUTE: Leads land where your team already works, not in another dashboard.
- BEN-SEE: You can see which sponsors engaged and what they asked. **[verify: admin dashboard shows this]**
- BEN-DECK: It sends your capabilities deck the moment a sponsor asks for it.
- BEN-FIT: It's configured around your services and your process, not a template.

### Proof (one, matched to the lead's country)
- PR-TR: It's live at NoblePath, an oncology CRO in Türkiye.
- PR-MX: It's live at Vertex Clinical Research in Mexico.
- PR-BOTH: It's live at two CROs, in Türkiye and Mexico.
- PR-TR-N / PR-MX-N: empty on purpose. Fill only with a number measured from Nova's own dashboard at that client.

### Ask (one)
- A1: Would a 48-hour demo built on your own material be worth a look? One word back is enough.
- A2: Worth a 48-hour demo on your own material? Reply yes and I'll set it up.

### Fixed
- Opt-out: "If this isn't relevant, reply 'no' and I won't follow up."
- Signature: from `NOVASCOUT_SENDER_*`.

---

## 5. Example — the real lead from 2026-09-30

Lead: Türkiye, site lists oncology and immunology, no trial on record.

**v2 (sent structure):**
> Your site lists oncology and immunology. How many pharma-team inquiries reach you only after that team has already moved on?
>
> We built Nova so an 11pm pharma-team inquiry is answered in real time and qualified, not the next morning — and lands in the tools you already use. It's live at NoblePath, an oncology CRO in Türkiye.
>
> Would a demo on your material be worth 48 hours of our time? One word back is enough.

**v3 (same facts, nothing invented):**

Subject: `Oncology sponsor inquiries after hours`

> Your site lists oncology and immunology. In those areas, a single sponsor inquiry can be a multi-million-dollar study, and sponsors often write outside your working hours, so that inquiry waits until morning while they contact the next CRO.
>
> We built an AI assistant for CRO websites that answers sponsors from your own service pages at any hour, collects their study brief, and books qualified calls into your calendar (we call it Nova). It's live at NoblePath, an oncology CRO in Türkiye.
>
> Would a 48-hour demo built on your own material be worth a look? One word back is enough.

About 100 words. One prospect fact. One angle (hours plus stakes). A description first, the name once in brackets. One proof. One ask.

---

## 6. Model and routing

- Drafting node: `claude-sonnet-5-5` via the Anthropic API. Check current API docs for request parameters rather than copying older Sonnet settings.
- Enrichment and scoring: unchanged, local qwen3.5:9b.
- If the API call fails, the lead stays queued and the next run retries. No fallback to the local model, so quality stays consistent.
- Use a dedicated API key for Nova Scout, with a monthly spend limit set in the Claude Console.

---

## 7. What to measure

- Reply rate and positive reply rate, tracked separately.
- Whether each approved draft was edited by a human before approval. If most need heavy edits, the claims list or the prompt needs work, not the model.

---

*Skill v3, 2026-09-30. Change claims in the table; change structure only with evidence from real replies.*
