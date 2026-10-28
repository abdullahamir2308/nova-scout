// The node half of rerender_sent_cro.py: reads the rows on stdin, runs the SHIPPED
// CRO renderers (drafting.json's Build First Touch and follow-ups.json's Build
// Follow-Up, everything above their "Node body") and compares byte for byte.
const fs = require('fs');
const path = require('path');

const WF = path.join(__dirname, '..', 'workflows');
function lib(file, node, names) {
  const wf = JSON.parse(fs.readFileSync(path.join(WF, file), 'utf8'));
  const src = wf.nodes.find((n) => n.name === node).parameters.jsCode;
  return new Function(src.slice(0, src.lastIndexOf('// Node body')) + '\nreturn {' + names.join(',') + '};')();
}
const D = lib('drafting.json', 'Build First Touch', ['renderFirstTouch', 'leadFacts']);
const F = lib('follow-ups.json', 'Build Follow-Up', ['renderFollowUp']);

const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
const norm = (s) => String(s || '').replace(/\r\n?/g, '\n').trim();
let same = 0;
const differ = [];
const counts = { first: 0, fu1: 0, fu2: 0 };
for (const r of rows) {
  const fu = /^follow-up-(\d+)\//.exec(r.variant);
  let out;
  if (fu) {
    counts['fu' + fu[1]]++;
    out = F.renderFollowUp({ lead_id: r.lead_id, n: Number(fu[1]), company_name: r.company_name, domain: r.domain,
      email: r.email, contact_name: r.contact_name, first_subject: r.first_subject, first_body: r.first_body,
      first_sent_at: r.first_sent_at });
  } else {
    counts.first++;
    const m = /\+ (\d+) recruiting trials? \+/.exec(r.subject || '');
    out = D.renderFirstTouch({ lead_id: r.lead_id, company_name: r.company_name, domain: r.domain, email: r.email,
      contact_name: r.contact_name, facts: D.leadFacts(r, m ? Number(m[1]) : 0) });
  }
  const variantCore = r.variant.split('+')[0];
  const ok = norm(out.body) === norm(r.sent_body) && out.subject === r.subject && out.variant === variantCore;
  if (ok) same++;
  else differ.push({ draft_id: r.draft_id, lead_id: r.lead_id, variant: r.variant,
    subject_same: out.subject === r.subject, body_same: norm(out.body) === norm(r.sent_body),
    variant_same: out.variant === variantCore, rendered_variant: out.variant,
    first_diff: (() => { const a = norm(out.body), b = norm(r.sent_body); let i = 0; while (i < a.length && a[i] === b[i]) i++;
      return i >= Math.max(a.length, b.length) ? null : { at: i, rendered: a.slice(Math.max(0, i - 30), i + 60), sent: b.slice(Math.max(0, i - 30), i + 60) }; })() });
}
console.log('sent CRO template emails: ' + rows.length + ' (first touches ' + counts.first + ', follow-up #1 ' + counts.fu1 +
  ', follow-up #2 ' + counts.fu2 + ')');
console.log('byte-identical (subject, body, variant): ' + same);
console.log('different: ' + differ.length);
differ.forEach((d) => console.log(JSON.stringify(d)));
process.exit(differ.length ? 1 : 0);
