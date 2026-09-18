# Nova Scout — Drafting Skill v2
## Copy rules for the local drafting model (Workflow 4) — first touch only

> Companion to NovaScout_MasterRef.md §9 Workflow 4. This governs *how persuasive* the first-touch email is.
> The master doc's grounding guard governs *what is true*. When they conflict, truth wins — always.
> The model reads this. Every rule is short, checkable, imperative. It is a 9B model.
> **Scope: the first email a prospect ever receives.** Follow-ups and reply payloads are a separate document (§8 points to what they need).

---

## 0. The one rule that cannot bend

**A fact about the prospect comes from the enrichment record or it does not appear.** No exceptions, no inference, no "likely."

Everything persuasive that is *not* a prospect fact comes from the **approved claims library** (§4) — lines a human wrote and owns. The model selects and inserts them. It does not compose them, and it never adds a statistic the library does not contain. A 9B model assembling approved pieces is safer *and* better than one writing sales copy freehand.

---

## 1. Why v1 drafts read flat (kept for the record)

Right: the hook referenced a decision the prospect made (a registered trial) — the correct kind of personalization; it beats city, title, or funding references.

Wrong: the hook had no consequence; there was no problem step at all; the value line was a feature list that read as if the prospect already owned Nova; the CTA made two asks; social proof named a customer with no outcome. Subject line was product-first.

---

## 2. Structure — every first touch, in this order

| # | Part | Source | Budget |
|---|---|---|---|
| — | **Subject** | hook fact or problem, never the product name | 30–50 chars |
| 1 | **Hook** — one prospect fact, about *them* | enrichment only | 1 sentence |
| 2 | **Problem** — a lost lead or lost sponsor, as a **question** | library | 1 sentence |
| 3 | **Outcome** — what changes for them + that it fits *their* stack | library | 1 sentence |
| 4 | **Proof** — one named live customer, geography-matched; real metric if one exists | library | 1 sentence |
| 5 | **Ask** — one action, "reply yes" form | library | 1 sentence |
| — | Opt-out line + signature | §5, fixed | fixed |

Body (1–5) stays **under 80 words** (locked in the master doc; the research agrees — 50–125 beats longer, and most opens are on a phone).

**Only the hook and subject are freely generated** from enrichment facts, under the grounding guard. Parts 2–5 are selected from the library and inserted verbatim or near-verbatim. This is the structural firewall against the "joining" failure: the model cannot fuse a prospect fact with a problem into a false causal claim when the problem line is a fixed question it did not write.

---

## 3. Rules the model must follow

**Subject**
- 30–50 characters. Specific to the prospect or the problem. Never the product name — product-name subjects read as ads.
- No "Re:" or "Fwd:" (fake-reply pattern, spam signal, and dishonest). No caps. No "free," "demo," "offer," "opportunity."
- Grounded like the hook: only enrichment facts.
- Good shapes: a prospect fact (`INM004 trial — a question`), or the problem (`Sponsor inquiries after hours`).

**Hook**
- One prospect fact, two at most.
- About *them* ("You're sponsoring…"), not about the source ("ClinicalTrials.gov lists…"). The source can stay for credibility; the sentence is about the prospect.
- Never open on founder name or city (existing rule).
- Never state a consequence of the fact as a fact. Consequences live in the problem line, as a question.

**Problem**
- Always a question. A question asserts nothing about the prospect — it cannot be fabrication, and they answer it themselves.
- Framed as **a lost lead, a lost sponsor, or lost pipeline** — the business loss, not the website mechanism. (Decision, 2026-09-18.)
- Selected from the library. Not composed.

**Outcome**
- Must contain "we built" or "Nova, which we built" — kills the "do I already own this?" ambiguity.
- Names what changes for them, not a feature.
- Carries the flexibility point in compressed form: the lead lands **where they already work**, no new tool forced on them. (Decision, 2026-09-18.) The full customization story is a follow-up asset, not a first-touch sentence.

**Proof**
- One named customer. Never two.
- Geography-matched: Vertex Clinical Research for Mexico and Latin America; NoblePath for Türkiye and nearby; either elsewhere. (Master doc §12.)
- **Only what is true.** If a real measured metric exists in the library, use it — it is the strongest line in the email. If none exists, name the customer and stop.
- **Never an industry statistic, a multiplier, or a figure about "this type of agent."** A generic number placed in an email about Nova reads as a claim about Nova, and it is the one claim a sophisticated buyer will ask to see. (Decision, 2026-09-18, after analysis: the 5–10x idea is replaced by the true-mechanism line O2/O3 and by pulling Nova's real dashboard metric.)

**Ask**
- Exactly one ask. The second offer waits for the follow-up.
- "Reply yes" form — the prospect's cost is one word.
- No scheduling link, no "30-minute call," no "quick chat" — all too large, too early, and links signal automation.

**Links — first touch**
- **Warm-up weeks 1–2: zero links.** The domain is at its most fragile; link count is a heavy spam signal on first-contact mail from a low-reputation sender.
- **After warm-up: at most one plain URL** (§5, locked), and if one, it is NoblePath's site — verifiable proof a CRO buyer will want to check.
- LinkedIn URL: not in the first touch (second link, common automation pattern; the phone in the signature is the stronger trust signal). Lives in the reply payload.
- PDF: never an attachment (top phishing signal, breaks plain-text). Lives in the reply payload as a hosted link.

**Everywhere**
- Banned adjectives stay banned (revolutionary, cutting-edge, innovative, and kin).
- No urgency the library does not supply.
- Plain text. (§5.)

---

## 4. Approved claims library — DRAFT, human confirms before first use

The only non-prospect sentences the model may use. A human edits these; the model never does. Every line is a claim Amitrix Labs stands behind.

### Problem — a lost lead, as a question (choose one)
- P1: When a sponsor shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?
- P2: How many sponsor inquiries reach you only after the sponsor has already moved on?
- P3: When a sponsor evaluates you after hours, does a qualified lead land with your team — or a contact form nobody sees until morning?

### Outcome — what changes, and that it fits their stack (choose one)
- O1: We built Nova to answer from your own material, qualify the lead, and route it wherever you already work — no new dashboard.
- O2: We built Nova so an 11pm sponsor inquiry is answered and qualified in seconds, not the next morning — and lands in the tools you already use.
- O3: Nova, which we built, answers sponsors from your own SOPs and service pages and hands your team a qualified lead, in your system, not ours.

### Proof — one named customer, geography-matched (choose one)
- PR-TR: It's live at NoblePath, an oncology CRO in Türkiye.
- PR-MX: It's live at Vertex Clinical Research in Mexico.
- PR-BOTH: It's live at two CROs, in Türkiye and Mexico.
- PR-TR-N / PR-MX-N: **empty on purpose.** Fill only with a number measured from Nova's own analytics at that client (conversations handled, leads captured, calls booked, over a stated period). When filled, it replaces the plain line above and becomes the strongest sentence in the email. Never fill from a web source.

### Ask (choose one)
- A1: Worth a 48-hour demo built on your own material? Reply yes and I'll set it up.
- A2: Would a demo on your material be worth 48 hours of our time? One word back is enough.
- A3: Want the 90-second recording of it running at NoblePath? Reply yes.

### Fixed
- Opt-out: "If this isn't relevant, reply 'no' and I won't follow up." (§5, locked)
- Signature: from `NOVASCOUT_SENDER_*` (§5, locked)

---

## 5. Before and after — same lead, same facts, nothing added

Lead: Argentina (→ Latin America → PR-MX), role inbox, one recruiting trial known. Warm-up week 1 → zero links.

**Before (v1, shipped):**
> Hello,
>
> ClinicalTrials.gov lists one recruiting trial with you as the sponsor called Efficacy of INM004 in Children With STEC-HUS. Nova is an assistant trained on your own SOPs, protocols and service pages that answers questions from your site visitors and your own staff. It already runs at NoblePath, an oncology CRO, and there is a 90-second recording of it. Would you like the recording or a demo built on your material in 48 hours?

**After (v2: hook + P1 + O1 + PR-MX + A1):**

Subject: `INM004 trial — a question`

> Hello,
>
> You're sponsoring a recruiting trial — Efficacy of INM004 in Children With STEC-HUS. When a sponsor shortlists you at 11pm, does that lead reach you by morning — or the next CRO on their list?
>
> We built Nova to answer from your own material, qualify the lead, and route it wherever you already work — no new dashboard. It's live at Vertex Clinical Research in Mexico.
>
> Worth a 48-hour demo built on your own material? Reply yes and I'll set it up.

75 words. Same prospect facts. Problem is now a lost lead, not a silent website. Outcome carries flexibility. Proof is same-region. One ask. Zero links. The recording, the PDF, NoblePath's site, and the LinkedIn profile are all intact — they moved to the reply.

---

## 6. What this skill cannot fix, and must not fake

**The strongest possible line needs a real number, and it is probably sitting in Nova's own dashboard.** "Live at Vertex" is a named customer; "Nova handled 140 sponsor conversations at Vertex in 60 days" is a named outcome, and one specific result outperforms any description or any industry figure. No metric has been measured in this project yet. The library slots (PR-TR-N, PR-MX-N) are empty by design. Fill them from Nova's analytics at the client — never from the web, never from a competitor's marketing, never as a multiplier.

**Copy is the fourth and fifth lever, not the first.** Deliverability → clean data → relevance → offer → personalization. The first three are built and verified. This skill sharpens the last two. It cannot rescue a lead mis-targeted upstream.

---

## 7. What to measure

Reply rate is the only metric (§5). Public benchmarks for orientation only: B2B cold email averages ~3–5% replies, top quartile 8–12%; lists under ~50 recipients roughly double the reply rate of large blasts. This pipeline — scored, disqualified, human-reviewed, 5–20/day — is structurally in the good bucket. Below ~3% after a real sample, the research says the problem is relevance upstream, not copy.

Track **positive** replies separately from total. Ten percent replies where nine are "no" is a different signal from ten where nine are "yes."

---

## 8. What lives outside this file (must exist before Send is published)

This skill governs the first touch only. Three things it deliberately leaves out have to be ready before the first real send, because a "yes" can arrive the same day:

1. **The reply payload** — what goes back when a prospect says yes. This is where the links belong: the 90-second recording, NoblePath's site, the one-page PDF, the LinkedIn profile, and the concrete demo-scheduling step. A "yes" that waits a day for a human is the exact problem Nova is sold to solve.
2. **The one-page PDF** — how Nova works at NoblePath and Vertex, and how it adapts to a prospect's own stack (their CRM, their sheets, their inbox — not ours). Real screenshots. Hosted, linked, never attached.
3. **Follow-up copy** — the master doc already makes follow-ups a template, not a model call (§9). They should do the one job the first touch didn't: add value or show proof. The recording is the natural follow-up #1.

---

*Skill version 2 — decisions of 2026-09-18 integrated. Change the library as claims change; change the structure only with evidence from real replies.*
