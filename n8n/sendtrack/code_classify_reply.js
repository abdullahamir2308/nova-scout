// Classify Inbound -- n8n Code node (Run Once for Each Item).
//
// Section 9, Workflow 6: "Any reply -> kill follow-up sequence, set
// status='replied'. Opt-out detection: reply matching no/unsubscribe/remove/
// stop -> add domain to blocklist, permanent." Section 5 makes that opt-out the
// basis of the GDPR/KVKK legitimate-interest position, so it is honoured the
// moment it is seen.
//
// Deterministic, no model (build rule 3). What decides the outcome is the text
// the person actually WROTE, not the message they sent back. Every reply quotes
// our email, and our email says "reply 'no'" -- classify the whole body and
// every single reply is an opt-out. So the quoted part is cut first, and the
// opt-out sentence is stripped again afterwards in case a client quoted it
// without a marker.
//
// Precedence: bounce > auto-reply > opt-out > reply. A bounce or an
// out-of-office is not a person answering, so neither sets 'replied' -- an
// out-of-office must not kill the follow-up sequence.

// Section 5 / Section 9 opt-out words -- parsed from the doc and checked at
// build time. "no" counts only as the FIRST word of what they wrote ("No,
// thanks" opts out; "we have no chatbot, tell me more" does not). The other
// three count anywhere in their text.
const OPT_OUT_KEYWORDS = ['no', 'unsubscribe', 'remove', 'stop'];
const OPT_OUT = "If this isn't relevant, reply 'no' and I won't follow up.";
const OWN_DOMAIN = __OWN_DOMAIN__;

// The first-word "no" in the languages of Section 12's geographies: the person
// following our instruction may not answer in English. Czech "ne" is left out
// on purpose -- it opens ordinary Turkish questions ("Ne zaman...?", "when..."),
// and a false opt-out blocklists a lead permanently.
const NO_WORDS = ['no', 'nope', 'não', 'nao', 'nie', 'hayır', 'hayir', 'nem', 'nu'];

// Replies from these are a person, not a company: the domain is never matched
// to a lead and never blocklisted.
const FREEMAIL = [
  'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.co.in', 'yahoo.com.br', 'yahoo.com.mx',
  'yahoo.com.ar', 'yahoo.ro', 'hotmail.com', 'outlook.com', 'live.com', 'msn.com', 'icloud.com',
  'me.com', 'aol.com', 'proton.me', 'protonmail.com', 'gmx.com', 'gmx.net', 'yandex.com',
  'mail.ru', 'mail.com', 'zoho.com', 'zohomail.com', 'rediffmail.com', 'wp.pl', 'o2.pl',
  'onet.pl', 'interia.pl', 'seznam.cz', 'freemail.hu', 'citromail.hu', 'uol.com.br',
  'bol.com.br', 'terra.com.br',
];

// "On Mon, 14 Sep 2026, X wrote:" and its equivalents in the leads' languages.
const QUOTE_HEADER = /(wrote|escribi[óo]|a [ée]crit|schrieb|escreveu|ha scritto|yazd[ıi]|napisa[łl](?:\(a\)|a)?|[íi]rta|napsal(?:\(a\)|a)?|a scris)\s*:\s*$/i;
const QUOTE_OPENER = /^\s*(on|el|le|am|em|il)\b/i;
const SEPARATOR = /^\s*(?:-{2,}\s*(?:original message|forwarded message|mensaje original|mensagem original|message d'origine)\s*-{2,}|_{10,})\s*$/i;
const OUTLOOK_FROM = /^\s*(from|de|von|od|kimden|feladó|expeditor)\s*:\s*\S/i;
const OUTLOOK_NEXT = /^\s*(sent|date|to|subject|enviado|fecha|para|asunto|gesendet|datum|an|betreff|wysłano|data|do|temat|tarih|kime|konu)\s*:/i;

const AUTO_SUBJECT = /^(?:auto(?:matic)?[ -]?(?:reply|response|antwort)|out of (?:the )?office|ooo\b|abwesenheit|fuera de (?:la )?oficina|respuesta autom|resposta autom|r[ée]ponse automatique|otomatik yan[ıi]t|automatyczna odpowied|automatick[áa] odpov|automatikus v[áa]lasz|r[ăa]spuns automat)/i;
const BOUNCE_SUBJECT = /undeliver|delivery status notification|returned mail|failure notice|delivery (?:has )?failed|mail delivery failed|non[- ]?delivery|could not be delivered/i;

function str(v) {
  return v === null || v === undefined ? '' : String(v).trim();
}

function domainOf(addr) {
  const s = str(addr).toLowerCase();
  const i = s.lastIndexOf('@');
  return i === -1 ? '' : s.slice(i + 1);
}

function addresses(v) {
  const found = String(v === null || v === undefined ? '' : v).match(/[^\s<>,;:"'()\[\]]+@[^\s<>,;:"'()\[\]]+/g) || [];
  const out = [];
  for (let i = 0; i < found.length; i++) {
    const a = found[i].toLowerCase().replace(/\.+$/, '');
    if (out.indexOf(a) === -1) out.push(a);
  }
  return out;
}

function messageId(v) {
  const m = str(v).match(/<[^<>\s]+>/);
  if (m) return m[0];
  const s = str(v).split(/\s+/)[0].replace(/[<>]/g, '');
  return s ? '<' + s + '>' : '';
}

// Every <...@...> token: In-Reply-To + References for a reply, the whole NDR
// body for a bounce (which quotes the original headers).
function messageIds(v) {
  const found = String(v || '').match(/<[^<>\s@]+@[^<>\s]+>/g) || [];
  return found.filter(function (x, i) { return found.indexOf(x) === i; });
}

function fallbackId(j) {
  const s = [str(j.date), str(j.from), str(j.subject)].join('|');
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return '<no-message-id.' + h.toString(16) + '@inbound.invalid>';
}

function stripHtml(h) {
  return String(h || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|tr|li|blockquote)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

// What they wrote above the quoted thread.
function freshText(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  let cut = lines.length;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    if (QUOTE_HEADER.test(ln)) {
      // Gmail wraps a long header over two lines: "On ..., X <" / "x@y> wrote:".
      cut = (i > 0 && !QUOTE_OPENER.test(ln) && QUOTE_OPENER.test(lines[i - 1])) ? i - 1 : i;
      break;
    }
    if (SEPARATOR.test(ln)) { cut = i; break; }
    if (OUTLOOK_FROM.test(ln) && lines.slice(i + 1, i + 5).some(function (w) { return OUTLOOK_NEXT.test(w); })) {
      cut = i;
      break;
    }
  }
  const kept = lines.slice(0, cut).filter(function (ln) { return !/^\s*>/.test(ln); });
  return kept.join('\n')
    .split(OPT_OUT).join(' ')
    .replace(/^\s*sent from my .*$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const GREETING = '(?:hi|hello|hey|dear|hola|ol[áa]|oi|merhaba|cze[śs][ćc]|dzie[ńn] dobry|szia|ahoj|bun[ăa]|salut|good (?:morning|afternoon|evening))';

function optOutKeyword(fresh) {
  let t = String(fresh || '').toLowerCase().trim();
  // A greeting is not the answer: "Hi Abdullah,\nNo thanks." and "Hi Abdullah,
  // no thanks." both open with "no" once the greeting is set aside.
  t = t.replace(new RegExp('^' + GREETING + '\\b[^\\n]{0,60}\\n'), '').trim();
  t = t.replace(new RegExp('^' + GREETING + '\\b[^,\\n]{0,40},\\s*'), '').trim();
  const first = (t.match(/^[^a-zÀ-ɏ]*([a-zÀ-ɏ]+)/) || [])[1] || '';
  if (NO_WORDS.indexOf(first) !== -1) return 'no';
  for (let i = 0; i < OPT_OUT_KEYWORDS.length; i++) {
    const k = OPT_OUT_KEYWORDS[i];
    if (k === 'no') continue;
    if (new RegExp('\\b' + k).test(t)) return k;
  }
  return null;
}

function classify(j) {
  const meta = j.metadata || {};
  const from = str(j.from);
  const fromAddr = addresses(from)[0] || '';
  const fromDomain = domainOf(fromAddr);
  const subject = str(j.subject);
  const raw = str(j.textPlain) || stripHtml(j.textHtml);
  const fresh = freshText(raw);

  const autoSubmitted = str(meta['auto-submitted']).toLowerCase();
  const isBounce = /mailer-daemon|postmaster|mail delivery (?:subsystem|system)/i.test(from) ||
    BOUNCE_SUBJECT.test(subject) ||
    /report-type="?delivery-status/i.test(str(meta['content-type']));
  const isAuto = (autoSubmitted !== '' && autoSubmitted !== 'no') ||
    str(meta['x-autoreply']) !== '' || str(meta['x-autorespond']) !== '' ||
    /^auto_reply$/i.test(str(meta['precedence'])) ||
    AUTO_SUBJECT.test(subject);
  const kw = isBounce || isAuto ? null : optOutKeyword(fresh);
  const classification = isBounce ? 'bounce' : isAuto ? 'auto-reply' : kw ? 'opt-out' : 'reply';

  const threadIds = messageIds(str(meta['in-reply-to']) + ' ' + str(meta['references']));
  const t = new Date(str(j.date)).getTime();
  return {
    message_id: messageId(meta['message-id']) || fallbackId(j),
    received_at: isNaN(t) ? null : new Date(t).toISOString(),
    from_addr: fromAddr,
    from_domain: fromDomain,
    freemail: FREEMAIL.indexOf(fromDomain) !== -1,
    own_domain: fromDomain === OWN_DOMAIN,
    subject: subject.slice(0, 300),
    classification: classification,
    opt_out_keyword: kw,
    thread_ids: isBounce ? threadIds.concat(messageIds(raw)).filter(function (x, i, a) { return a.indexOf(x) === i; }) : threadIds,
    mentioned_addrs: isBounce ? addresses(raw).filter(function (a) { return domainOf(a) !== OWN_DOMAIN; }) : [],
    reply_text: fresh.slice(0, 4000),
    body_excerpt: fresh.slice(0, 600),
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

return { json: { payload: classify($input.item.json) } };
