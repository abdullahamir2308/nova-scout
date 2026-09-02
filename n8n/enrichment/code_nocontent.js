// Record No Content — n8n Code node (Run Once for Each Item).
// A lead whose site would not load, or which returned a JS-shell with no
// readable text, still has to leave the 'ingested' queue or it blocks the
// batch forever (Master Ref build rule 4: queue-driven and idempotent).
// It advances to 'enriched' carrying an explicit failure marker, which is
// exactly what Workflow 3's "no functioning website" disqualifier reads.

const src = $input.item.json;
const reason = src.fetch_ok
  ? 'SITE RETURNED NO READABLE TEXT (likely JavaScript-rendered): ' + src.prompt_chars + ' chars extracted'
  : 'SITE UNREACHABLE: ' + (src.fetch_errors || []).join('; ').slice(0, 300);

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
      site_quality_notes: reason,
      raw_extraction: {
        enrichment_status: src.fetch_ok ? 'no_readable_text' : 'unreachable',
        domain: src.domain,
        is_cro: null,
        company_type: null,
        founder_title: null,
        city: null,
        site_functional: false,
        fetch: {
          ok: src.fetch_ok,
          reached_via: src.reached_via,
          pages_fetched: src.pages_fetched,
          discovered: src.discovered,
          anchors_seen: src.anchors_seen,
          errors: src.fetch_errors,
          prompt_chars: src.prompt_chars,
        },
        model: null,
        extracted_at: new Date().toISOString(),
      },
    },
  },
};
