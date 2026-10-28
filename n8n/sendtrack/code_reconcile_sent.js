// Plan Sent Reconcile -- n8n Code node (Run Once for All Items).
//
// Section 9, Workflow 6 (2026-10-28). The Sent trigger only reacts to a NEW
// message: a send that lands in the Sent folder while its IMAP connection is
// down is never delivered to it, even after a reconnect, a publish or a restart
// (n8n's EmailReadImap v2 reads the folder only on an arrival). Send then fails
// closed `sent-mirror-behind`, and since Send is what puts new mail in Sent,
// nothing arrives to wake the trigger: a deadlock. On 2026-10-28 it held from
// 07:55 until a hand repair at 08:22 UTC.
//
// So Mailbox Watch also reads the folder on its own clock -- every 10 minutes,
// and when n8n starts or the workflow is published -- through the imap-health
// checker's read-only listing (imap_preflight.py: EXAMINE, BODY.PEEK), and
// hands that listing to Reconcile Sent, which is Mirror Sent's own upsert.
//
// THE SAFETY RULES, all of them here or in Reconcile Sent:
//   * only a listing the checker says is complete counts: `ok` true and `sent`
//     an array. Anything else -- the checker down, the IMAP login refused, a
//     half answer -- reconciles NOTHING (listed: false). Never mark seen what
//     is not known to be in the folder.
//   * a message is matched on its Message-ID, exactly as the trigger path keys
//     it (messageId() from code_mirror_sent.js, placed above this file by the
//     build). An entry without a well-formed Message-ID is skipped and counted,
//     never keyed on a fallback hash -- the trigger's fallback hashes headers the
//     listing does not reproduce byte for byte, and a different hash would be a
//     second row for one message.
//   * the rows are built by the trigger path's own sentRows(), so a message
//     the reconcile inserts is the row the trigger would have written.
//   * Reconcile Sent upserts on Message-ID (the primary key): a message already
//     in the mirror only gets seen_in_sent_folder = true, never a second row.

const RECONCILE_ID_RE = /^<[^<>\s]+>$/;

// The checker's answer -> the items sentRows() reads (the IMAP trigger's
// "simple" shape: date, to, cc, metadata.message-id, metadata.bcc).
function listingItems(check) {
  const c = check || {};
  if (c.ok !== true || !Array.isArray(c.sent)) {
    return { listed: false, items: [], skipped: 0,
      why: c.error !== undefined ? 'the checker did not answer' : c.ok === false
        ? 'the checker answered but its IMAP check failed (' + str(c.stage) + ')' : 'no usable listing' };
  }
  const items = [];
  let skipped = 0;
  for (let i = 0; i < c.sent.length; i++) {
    const e = c.sent[i] || {};
    const id = str(e.message_id);
    if (!RECONCILE_ID_RE.test(id) || messageId(id) !== id || !str(e.date)) {
      skipped++;
      continue;
    }
    items.push({ date: str(e.date), to: str(e.to), cc: str(e.cc), subject: '',
      metadata: { 'message-id': id, bcc: str(e.bcc) } });
  }
  return { listed: true, items: items, skipped: skipped, why: null };
}

function reconcilePlan(check, operatorEmail) {
  const l = listingItems(check);
  const r = sentRows(l.items, operatorEmail);
  return {
    listed: l.listed,
    why: l.why,
    // Reconcile Sent reads `rows`, `seen` and `undated`, as Mirror Sent does.
    payload: { listed: l.listed, rows: r.rows, seen: l.items.length, undated: r.undated,
      skipped: l.skipped, hours: Number((check || {}).hours) || null },
  };
}

// ---------------------------------------------------------------------------
// Node body
// ---------------------------------------------------------------------------

// Reconcile Settings (a Postgres node, executeOnce) sits between the checker
// and this node, so the listing is read back from its node by name.
const settings = $input.first().json;
const check = $('List Sent Folder').first().json;
return [{ json: reconcilePlan(check, settings.operator_email) }];
