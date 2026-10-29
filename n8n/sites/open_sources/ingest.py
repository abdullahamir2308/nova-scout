"""Write verified research sites into leads + site_lead_sources (migration 025).

    python n8n/sites/open_sources/ingest.py VERIFIED.json [--db novascout] [--apply] [--limit N]

Dry run by default: prints what would be written, writes nothing. --apply runs
the same statement and commits.

A verified site becomes leads(source = 'sites-ctgov' | 'sites-overture',
status = 'ingested') and is from then on an ordinary lead: Enrichment, Scoring,
Contact Lookup, Drafting - Research Sites. The rules on the way in:
  * only outcome 'resolved' (name AND city on the site's own pages) and a
    robots.txt that lets a crawler read the homepage;
  * never an existing lead: ON CONFLICT (domain) DO NOTHING, so a CRO, a
    Trialsites lead or anything else already in `leads` is untouched;
  * never a blocklisted domain;
  * never a source a niche holds (niches.held_sources, migration 026): the
    operator stopped the Overture harvest on 2026-10-30 until its precision
    audit passes, so its rows are counted as `held_source` and not written;
  * no contact of any kind: Contact Lookup reads the site's own published
    address later, the only place an address may come from.
One statement, one transaction, idempotent: running it twice writes nothing the
second time.
"""
import argparse
import collections
import json
import subprocess
import sys

SQL = r"""
WITH p AS (SELECT %(payload)s::jsonb AS a),
rows AS (
  SELECT r->>'domain' AS domain, r->>'company_name' AS company_name, r->>'country' AS country, r->>'source' AS source,
         r
    FROM p, jsonb_array_elements(p.a) r
   WHERE NOT EXISTS (SELECT 1 FROM blocklist b WHERE lower(r->>'domain') = b.domain OR lower(r->>'domain') LIKE '%%.' || b.domain)
     AND NOT EXISTS (SELECT 1 FROM niches n WHERE r->>'source' = ANY (n.held_sources))
),
ins AS (
  INSERT INTO leads (domain, company_name, country, source, status)
  SELECT domain, company_name, country, source, 'ingested' FROM rows
  ON CONFLICT (domain) DO NOTHING
  RETURNING id, domain, source, country
),
src AS (
  INSERT INTO site_lead_sources (lead_id, source, facility_name, city, state, country, ctgov_recruiting, ctgov_ncts,
                                 overture_id, overture_licences, name_kind, has_contact_form, has_mailto,
                                 robots_allowed, verify_detail)
  SELECT i.id, i.source, nullif(r.r->>'facility_name', ''), nullif(r.r->>'city', ''), nullif(r.r->>'state', ''),
         r.country, coalesce((r.r->>'ctgov_recruiting')::int, 0),
         coalesce(ARRAY(SELECT jsonb_array_elements_text(r.r->'ctgov_ncts')), '{}'),
         r.r->>'overture_id', coalesce(ARRAY(SELECT jsonb_array_elements_text(r.r->'licences')), '{}'),
         nullif(r.r->>'name_kind', ''), (r.r->>'has_contact_form')::boolean, (r.r->>'has_mailto')::boolean,
         true, left(r.r->>'verify_detail', 500)
    FROM ins i JOIN rows r ON r.domain = i.domain
  RETURNING lead_id
)
SELECT json_build_object(
  'offered', (SELECT count(*) FROM p, jsonb_array_elements(p.a)),
  'held_source', (SELECT count(*) FROM p, jsonb_array_elements(p.a) r
                    WHERE EXISTS (SELECT 1 FROM niches n WHERE r->>'source' = ANY (n.held_sources))),
  'not_blocklisted_or_held', (SELECT count(*) FROM rows),
  'new_leads', (SELECT count(*) FROM ins),
  'source_rows', (SELECT count(*) FROM src),
  'by_source', (SELECT coalesce(json_object_agg(source, n), '{}') FROM (SELECT source, count(*) n FROM ins GROUP BY 1) x),
  'by_country', (SELECT coalesce(json_object_agg(country, n), '{}') FROM (SELECT country, count(*) n FROM ins GROUP BY 1) x));
"""


def rows_from(verified):
    out = []
    for v in verified.values():
        if v.get("outcome") != "resolved" or not v.get("domain") or v.get("robots_allowed") is not True:
            continue
        c = v["candidate"]
        out.append({
            "domain": v["domain"].lower(), "company_name": c["name"], "country": c["country"], "source": c["source"],
            "facility_name": c.get("facility_name"), "city": c.get("city"), "state": c.get("state"),
            "ctgov_recruiting": c.get("ctgov_recruiting") or 0, "ctgov_ncts": c.get("ctgov_ncts") or [],
            "overture_id": c.get("overture_id"), "licences": c.get("licences") or [],
            "name_kind": "research-word" if c.get("research_name") else None,
            "has_contact_form": v.get("has_contact_form"), "has_mailto": v.get("has_mailto"),
            "verify_detail": v.get("detail"),
        })
    # One lead per domain (two hosts can fold to one domain): most recruiting first.
    best = {}
    for r in sorted(out, key=lambda r: (-r["ctgov_recruiting"], r["source"] != "sites-ctgov", r["company_name"])):
        best.setdefault(r["domain"], r)
    return list(best.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("verified")
    ap.add_argument("--db", default="novascout")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    rows = rows_from(json.load(open(a.verified, encoding="utf-8")))
    if a.limit:
        rows = rows[:a.limit]
    print("resolved, robots-allowed, one per domain:", len(rows),
          dict(collections.Counter(r["source"] for r in rows)))
    payload = "$nsjson$" + json.dumps(rows, ensure_ascii=False) + "$nsjson$"
    assert "$nsjson$" not in json.dumps(rows, ensure_ascii=False)
    script = "BEGIN;\n" + SQL % {"payload": payload} + ("COMMIT;\n" if a.apply else "ROLLBACK;\n")
    res = subprocess.run(["docker", "exec", "-i", "nova-scout-postgres-1", "psql", "-U", "novascout", "-d", a.db,
                          "-At", "-v", "ON_ERROR_STOP=1"], input=script, capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        sys.exit(res.stderr)
    result = [ln for ln in res.stdout.splitlines() if ln.startswith("{")]
    print(("APPLIED: " if a.apply else "dry run (rolled back): ") + (result[0] if result else res.stdout))


if __name__ == "__main__":
    main()
