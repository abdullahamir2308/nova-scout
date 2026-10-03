"""The auto-approval chain as n8n nodes -- one generator for Workflow 4 (Drafting)
and Follow-Ups, so the two workflows carry the same chain (migration 014).

    Approval Gate -> Needs Claim Check? -> Claude Claim Check -> Apply Claim Check
      -> Needs Repair 1? -> Claude Repair 1 -> Apply Repair 1 -> Repaired 1?
      -> <Assemble> R1 -> Approval Gate R1 -> Needs Claim Check R1? -> Claude Claim Check R1
      -> Apply Claim Check R1 -> Needs Repair 2? -> ... -> Apply Claim Check R2 -> <write>

Every "no" exit goes straight to the write node, carrying the draft as decided:
approved by the workflow, or held with every attempt's reasons. The repair
rounds are unrolled, not an n8n loop: each Code node reads the item it follows
by name ($('Approval Gate R1').item), which a loop would break, and an unrolled
chain can be guarded node by node. code_approval.js's MAX_REPAIRS decides how
many rounds there are.

The code is drafting's: code_approval.js (Approval Gate, every copy, verbatim),
its rules section + code_approval_apply.js (Apply Claim Check), its rules
section + code_approval_repair.js (Apply Repair). The Assemble copy in each
round is the workflow's own Assemble node code, verbatim -- a repaired email
runs every rule again because it runs the same code again.
"""
import io
import os
import re

MARKER = "// Node body"


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def load(drafting_dir):
    gate = _read(os.path.join(drafting_dir, "code_approval.js"))
    at = gate.find(MARKER)
    assert at != -1, "code_approval.js has no '%s' marker" % MARKER
    rules = gate[:at]
    assert "$(" not in rules and "$input" not in rules, (
        "the rules section of code_approval.js reads n8n data -- node-body code has moved above its 'Node body' "
        "marker, and Apply Claim Check and Apply Repair (which embed that section) would run it again")
    m = re.search(r"^const MAX_REPAIRS = (\d+);", rules, re.M)
    assert m, "MAX_REPAIRS not found in code_approval.js"
    max_repairs = int(m.group(1))
    assert 1 <= max_repairs <= 3, "MAX_REPAIRS = %d -- the chain is unrolled; keep it small" % max_repairs
    apply_src = _read(os.path.join(drafting_dir, "code_approval_apply.js"))
    repair_src = _read(os.path.join(drafting_dir, "code_approval_repair.js"))
    assert apply_src.count("$('__GATE__')") == 1, "code_approval_apply.js must read $('__GATE__') exactly once"
    assert repair_src.count("$('__CHECKED__')") == 1, "code_approval_repair.js must read $('__CHECKED__') exactly once"
    # A repair is the same model as the check and the drafting node, with the
    # same parameters -- never a cheaper one.
    assert re.search(r"function repairRequest\(ctx, flagged\) \{\s*return \{\s*model: CHECK_MODEL,\s*"
                     r"max_tokens: CHECK_MAX_TOKENS,\s*output_config: \{ effort: CHECK_EFFORT,", rules), (
        "the repair request no longer uses the claim check's model, max_tokens and effort")
    return {"gate": gate, "rules": rules, "apply": rules + "\n" + apply_src, "repair": rules + "\n" + repair_src,
            "max_repairs": max_repairs}


def _bake(template, token, name):
    needle = "$('%s')" % token
    assert template.count(needle) == 1, "%s placeholder not found exactly once" % token
    return template.replace(needle, "$('%s')" % name)


def names(max_repairs, assemble):
    """Every node name of the chain, by round. Round 0 keeps the names the chain
    had before the repair loop."""
    rounds = [{"gate": "Approval Gate", "needs_check": "Needs Claim Check?", "check": "Claude Claim Check",
               "apply": "Apply Claim Check"}]
    for k in range(1, max_repairs + 1):
        rounds.append({"needs_repair": "Needs Repair %d?" % k, "repair": "Claude Repair %d" % k,
                       "apply_repair": "Apply Repair %d" % k, "repaired": "Repaired %d?" % k,
                       "assemble": "%s R%d" % (assemble, k), "gate": "Approval Gate R%d" % k,
                       "needs_check": "Needs Claim Check R%d?" % k, "check": "Claude Claim Check R%d" % k,
                       "apply": "Apply Claim Check R%d" % k})
    return rounds


def _if(name, cid, expr, pos, notes):
    return {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{"id": cid, "leftValue": expr, "rightValue": True,
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and",
            },
            "options": {},
        },
        "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos, "notes": notes,
    }


def _code(name, src, pos, notes):
    return {"parameters": {"mode": "runOnceForEachItem", "jsCode": src}, "name": name,
            "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos, "notes": notes}


def _claude(name, body_field, pos, cred, notes):
    return {
        "parameters": {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "anthropicApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.%s) }}" % body_field,
            "options": {"timeout": 300000},
        },
        "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos,
        "credentials": cred, "onError": "continueRegularOutput", "retryOnFail": True, "maxTries": 2,
        "waitBetweenTries": 5000, "notes": notes,
    }


def _edge(*targets):
    return {"main": [[{"node": t, "type": "main", "index": 0} for t in targets]]}


def _branch(true_target, false_target):
    return {"main": [[{"node": true_target, "type": "main", "index": 0}],
                     [{"node": false_target, "type": "main", "index": 0}]]}


def build(code, assemble, assemble_js, write, cred, model, effort, origin):
    """(nodes, connections) for the whole chain, from Approval Gate to `write`.
    The caller connects its Assemble (and anything else that needs judging) to
    'Approval Gate', and owns the write node."""
    x0, y0 = origin
    rounds = names(code["max_repairs"], assemble)
    nodes, conns = [], {}

    r0 = rounds[0]
    nodes += [
        _code(r0["gate"], code["gate"], [x0, y0],
              "Auto-approval, rules 1-4 (migration 014): an EMAIL draft goes on to the claim check only if "
              "settings.auto_approve_email is on, it carries no rule tag, it is not low-context, and every claim code "
              "it records is confirmed. Anything else is held -- written 'pending' with hold_reason -- and no check is "
              "paid for. A LinkedIn draft is always held (Section 6). The database re-checks all of this on the write. "
              "Same code in Workflow 4 and Follow-Ups (n8n/drafting/code_approval.js)."),
        _if(r0["needs_check"], "needscheck", "={{ $json.needs_check }}", [x0 + 220, y0],
            "true: an email draft that passed rules 1-4. false: everything is held; straight to the write."),
        _claude(r0["check"], "check_request", [x0 + 440, y0 - 100], cred,
                "Auto-approval, rule 5: a second %s call (effort %s, Section 3's parameters) that compares each "
                "product claim with the confirmed claims and each prospect fact with the record the draft was "
                "written from, sentence by sentence. A widened claim fails. Errors continue as items: the draft is "
                "held for a human. No fallback to another model." % (model, effort)),
        _code(r0["apply"], _bake(code["apply"], "__GATE__", r0["gate"]), [x0 + 660, y0 - 100],
              "Approved by the workflow only if every statement the check found is supported AND its quotes cover the "
              "whole email. A hold that names widened or unsupported statements goes to a repair (up to %d); any other "
              "hold is final. The verdicts go into drafts.claim_check either way." % code["max_repairs"]),
    ]
    conns[r0["gate"]] = _edge(r0["needs_check"])
    conns[r0["needs_check"]] = _branch(r0["check"], write)
    conns[r0["check"]] = _edge(r0["apply"])

    prev_apply = r0["apply"]
    x = x0 + 880
    for k, r in enumerate(rounds[1:], 1):
        y = y0 - 100 - 100 * k
        nodes += [
            _if(r["needs_repair"], "needsrepair%d" % k, "={{ $json.needs_repair }}", [x, y + 100],
                "Repair %d of %d: true when the claim check held the email for statements that say more than their "
                "source. false: approved, or held for good -- straight to the write." % (k, code["max_repairs"])),
            _claude(r["repair"], "repair_request", [x + 220, y], cred,
                    "Repair %d: the drafting model (%s, effort %s) is sent the email and the checker's exact reasons "
                    "and rewrites only the flagged sentences. Errors continue as items: the draft is held with every "
                    "reason so far." % (k, model, effort)),
            _code(r["apply_repair"], _bake(code["repair"], "__CHECKED__", prev_apply), [x + 440, y],
                  "Applies the rewrites BY CODE, only to the flagged sentences: nothing the checker did not flag can "
                  "change. Emits the repaired composition shaped like a model response, for the Assemble copy next."),
            _if(r["repaired"], "repaired%d" % k, "={{ $json.repaired }}", [x + 660, y],
                "false: the repair call failed or changed nothing -- held, straight to the write."),
            _code(r["assemble"], assemble_js, [x + 880, y],
                  "%s, the same code: every rule runs again on the repaired email." % assemble),
            _code(r["gate"], code["gate"], [x + 1100, y],
                  "Rules 1-4 again on the repaired email -- the same code as Approval Gate. A rewrite that broke a "
                  "rule is held here, with every earlier attempt's reasons."),
            _if(r["needs_check"], "needscheck%d" % k, "={{ $json.needs_check }}", [x + 1320, y],
                "true: the repaired email passed rules 1-4."),
            _claude(r["check"], "check_request", [x + 1540, y - 100], cred,
                    "The claim check again on the repaired email -- the same request, the same model."),
            _code(r["apply"], _bake(code["apply"], "__GATE__", r["gate"]), [x + 1760, y - 100],
                  "Rule 5 again. %s" % ("Another hold with flagged statements goes to the next repair."
                                        if k < code["max_repairs"] else
                                        "The last round: a hold here is final, with every attempt's reasons.")),
        ]
        conns[r["needs_repair"]] = _branch(r["repair"], write)
        conns[r["repair"]] = _edge(r["apply_repair"])
        conns[r["apply_repair"]] = _edge(r["repaired"])
        conns[r["repaired"]] = _branch(r["assemble"], write)
        conns[r["assemble"]] = _edge(r["gate"])
        conns[r["gate"]] = _edge(r["needs_check"])
        conns[r["needs_check"]] = _branch(r["check"], write)
        conns[r["check"]] = _edge(r["apply"])
        conns[prev_apply] = _edge(r["needs_repair"])
        prev_apply = r["apply"]
        x += 1980
    conns[prev_apply] = _edge(write)
    return nodes, conns, [x, y0 + 100]


def exits(rounds):
    """The node names whose outputs reach the write node, for a guard."""
    out = [rounds[0]["needs_check"]]
    for r in rounds[1:]:
        out += [r["needs_repair"], r["repaired"], r["needs_check"]]
    out.append(rounds[-1]["apply"])
    return sorted(out)
