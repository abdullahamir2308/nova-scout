// Normalise Extraction — n8n Code node (Run Once for Each Item).
// Parses the Ollama response, coerces types, and resolves founder_linkedin
// deterministically. The LLM never picks the URL: it only names the founder,
// and this node matches that name against the URLs regex already harvested
// from the page (Master Ref build rule 3 — no tokens on deterministic work).

const src = $('Fetch Site Pages').item.json;
const resp = $input.item.json;

const CANONICAL_PHASES = {
  'phase i': 'Phase I', 'phase 1': 'Phase I', 'fase i': 'Phase I', 'fase 1': 'Phase I', i: 'Phase I', '1': 'Phase I',
  'phase ii': 'Phase II', 'phase 2': 'Phase II', 'fase ii': 'Phase II', 'fase 2': 'Phase II', ii: 'Phase II', '2': 'Phase II',
  'phase iii': 'Phase III', 'phase 3': 'Phase III', 'fase iii': 'Phase III', 'fase 3': 'Phase III', iii: 'Phase III', '3': 'Phase III',
  'phase iv': 'Phase IV', 'phase 4': 'Phase IV', 'fase iv': 'Phase IV', 'fase 4': 'Phase IV', iv: 'Phase IV', '4': 'Phase IV',
  bioequivalence: 'Bioequivalence', bioequivalencia: 'Bioequivalence', be: 'Bioequivalence',
};

function deaccent(s) {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

function matchFounderLinkedin(founderName, candidates) {
  if (!founderName || !candidates || !candidates.length) return null;
  const parts = deaccent(String(founderName).toLowerCase()).split(/[^a-z]+/).filter((p) => p.length >= 3);
  if (!parts.length) return null;
  let best = null;
  let bestHits = 0;
  for (const url of candidates) {
    const m = String(url).match(/linkedin\.com\/in\/([^/?#]+)/i);
    if (!m) continue;
    const slug = deaccent(decodeURIComponent(m[1]).toLowerCase());
    const hits = parts.filter((p) => slug.includes(p)).length;
    if (hits > bestHits) {
      bestHits = hits;
      best = url;
    } else if (hits === bestHits && hits > 0 && best && url !== best) {
      best = null; // ambiguous tie -> refuse to guess
    }
  }
  const needed = parts.length >= 2 ? 2 : 1;
  return bestHits >= needed ? best : null;
}

function cleanStr(v, max) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (!s || s.toLowerCase() === 'null' || s.toLowerCase() === 'n/a' || s === '-') return null;
  return s.slice(0, max || 500);
}

// The HTTP node is set to continue on error, so a failed call arrives without
// a `response` field. Treat that as an extraction failure, not a silent null row.
let llm = null;
let parseError = null;
let callFailed = false;
const rawText = resp && typeof resp.response === 'string' ? resp.response : null;

if (rawText === null) {
  callFailed = true;
  parseError = 'ollama call failed: ' + JSON.stringify(resp && resp.error ? resp.error : resp).slice(0, 300);
} else {
  try {
    llm = JSON.parse(rawText);
  } catch (e) {
    // Grammar-constrained output should always parse; fall back to a fenced-block
    // rescue rather than losing the lead.
    const m = rawText.match(/\{[\s\S]*\}/);
    if (m) {
      try {
        llm = JSON.parse(m[0]);
      } catch (e2) {
        parseError = 'unparseable JSON: ' + e2.message;
      }
    } else {
      parseError = 'no JSON object in response: ' + rawText.slice(0, 200);
    }
  }
}

// Distinguish a systemic failure from a lead-specific one.
//   No response at all (Ollama down, timeout, 5xx) -> do NOT advance the lead.
//     It stays 'ingested' and the next cron run retries it, so an outage is
//     self-healing instead of marching the whole queue to 'enriched' with
//     failure markers that then need a manual reset.
//   A response that arrived but would not parse -> advance with a marker, so
//     one poisoned lead cannot block the queue forever.
if (callFailed) {
  return {
    json: {
      write: false,
      lead_id: src.lead_id,
      domain: src.domain,
      skip_reason: parseError,
    },
  };
}

if (parseError) {
  return {
    json: {
      write: true,
      payload: {
        lead_id: src.lead_id,
        therapeutic_areas: [],
        phases: [],
        founder_name: null,
        founder_linkedin: null,
        employee_estimate: null,
        has_chatbot: src.has_chatbot,
        chatbot_vendor: src.chatbot_vendor,
        site_quality_notes: 'EXTRACTION FAILED: ' + parseError.slice(0, 400),
        raw_extraction: {
          enrichment_status: 'extraction_failed',
          error: parseError,
          raw_response: rawText ? rawText.slice(0, 2000) : null,
          fetch: {
            ok: src.fetch_ok,
            reached_via: src.reached_via,
            pages_fetched: src.pages_fetched,
            errors: src.fetch_errors,
            prompt_chars: src.prompt_chars,
          },
          domain: src.domain,
          extracted_at: new Date().toISOString(),
        },
      },
    },
  };
}

const areas = Array.isArray(llm.therapeutic_areas) ? llm.therapeutic_areas : [];
const therapeutic_areas = Array.from(
  new Set(areas.map((a) => cleanStr(a, 80)).filter(Boolean).map((a) => a.toLowerCase()))
).slice(0, 25);

const rawPhases = Array.isArray(llm.phases) ? llm.phases : [];
const phases = Array.from(
  new Set(
    rawPhases
      .map((p) => cleanStr(p, 40))
      .filter(Boolean)
      .map((p) => CANONICAL_PHASES[p.toLowerCase().trim()] || null)
      .filter(Boolean)
  )
);

let employee_estimate = null;
if (llm.employee_estimate !== null && llm.employee_estimate !== undefined) {
  const n = Number(llm.employee_estimate);
  if (Number.isFinite(n) && n > 0 && n < 200000) employee_estimate = Math.round(n);
}

const founder_name = cleanStr(llm.founder_name, 200);
const founder_linkedin = matchFounderLinkedin(founder_name, src.linkedin_candidates);

return {
  json: {
    write: true,
    payload: {
      lead_id: src.lead_id,
      therapeutic_areas,
      phases,
      founder_name,
      founder_linkedin,
      employee_estimate,
      has_chatbot: src.has_chatbot,
      chatbot_vendor: src.chatbot_vendor,
      site_quality_notes: cleanStr(llm.site_quality_notes, 2000),
      raw_extraction: {
        enrichment_status: 'ok',
        domain: src.domain,
        // Fields with no column of their own in Section 8, kept here because
        // Workflow 3 disqualifiers and Workflow 4 grounding need them.
        is_cro: typeof llm.is_cro === 'boolean' ? llm.is_cro : null,
        company_type: cleanStr(llm.company_type, 120),
        founder_title: cleanStr(llm.founder_title, 200),
        city: cleanStr(llm.city, 120),
        founder_linkedin_candidates: src.linkedin_candidates,
        founder_linkedin_matched: founder_linkedin !== null,
        llm_raw: llm,
        fetch: {
          ok: src.fetch_ok,
          reached_via: src.reached_via,
          pages_fetched: src.pages_fetched,
          discovered: src.discovered,
          anchors_seen: src.anchors_seen,
          errors: src.fetch_errors,
          prompt_chars: src.prompt_chars,
        },
        model: 'qwen3.5:9b',
        extracted_at: new Date().toISOString(),
      },
    },
  },
};
