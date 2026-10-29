"""Every Ollama call in the shipped workflows asks for the same model the same way.

    python n8n/scoring/test_ollama_calls.py

Operator brief 2026-10-30: one copy of the model, unloaded after 5 idle
minutes. Ollama keeps one runner per model and loads a second copy (or
reloads the runner) for a request with a different model tag or num_ctx, and
a request's keep_alive overrides the server's OLLAMA_KEEP_ALIVE. So every
HTTP node in n8n/workflows/*.json that calls Ollama must carry the same
model, num_ctx and keep_alive -- and the keep-alive must be 5 minutes.
"""
import glob
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKFLOWS = os.path.join(REPO, "n8n", "workflows")
EXPECTED_KEEP_ALIVE = "5m"
EXPECTED_CALLERS = {("enrichment.json", "Ollama Extract"), ("scoring.json", "Ollama Rationale")}


def ollama_nodes():
    for path in sorted(glob.glob(os.path.join(WORKFLOWS, "*.json"))):
        with io.open(path, encoding="utf-8") as fh:
            wf = json.load(fh)
        for node in wf.get("nodes", []):
            if ":11434" in json.dumps(node.get("parameters", {})):
                yield os.path.basename(path), node


def field(body, pattern):
    found = re.findall(pattern, body)
    return found[0] if len(found) == 1 else ("<%d matches>" % len(found))


def main():
    passed = failed = 0

    def check(name, ok, got=None):
        nonlocal passed, failed
        if ok:
            passed += 1
            print("  [PASS] " + name)
        else:
            failed += 1
            print("  [FAIL] " + name + ("" if got is None else "  -- got %r" % (got,)))

    calls = {}
    for wf, node in ollama_nodes():
        p = node["parameters"]
        body = p.get("jsonBody", "")
        calls[(wf, node["name"])] = {
            "url": p.get("url"),
            "model": field(body, r"\bmodel: '([^']*)'"),
            "keep_alive": field(body, r"\bkeep_alive: '([^']*)'"),
            "num_ctx": field(body, r"\bnum_ctx: (\d+)"),
        }

    check("the Ollama callers are exactly Enrichment's and Scoring's", set(calls) == EXPECTED_CALLERS, sorted(calls))
    for key in ("url", "model", "num_ctx", "keep_alive"):
        values = {c[key] for c in calls.values()}
        check("every call uses one %s" % key, len(values) == 1 and not next(iter(values)).startswith("<"), values)
    models = {c["model"] for c in calls.values()}
    check("the model is named by an explicit tag", all(re.fullmatch(r"[\w.\-]+:[\w.\-]+", m) for m in models), models)
    check("keep_alive is %s" % EXPECTED_KEEP_ALIVE, {c["keep_alive"] for c in calls.values()} == {EXPECTED_KEEP_ALIVE},
          {c["keep_alive"] for c in calls.values()})
    check("num_ctx stays capped at 16K-32K (MasterRef section 3)",
          all(16384 <= int(c["num_ctx"]) <= 32768 for c in calls.values() if c["num_ctx"].isdigit()),
          {c["num_ctx"] for c in calls.values()})
    # The enrichment record names the model that wrote it; it must be the one called.
    with io.open(os.path.join(REPO, "n8n", "enrichment", "code_normalise.js"), encoding="utf-8") as fh:
        labels = set(re.findall(r"\bmodel: '([^']*)'", fh.read()))
    check("Normalise Extraction records the model that was called", labels == models, (labels, models))

    print("%d passed, %d failed" % (passed, failed))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
