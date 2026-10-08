#!/usr/bin/env python3
"""Prompt-caching cost report, read from real n8n executions.

Section 3 / Section 4: every Sonnet 5.5 call's `usage` block is kept in n8n's
own `execution_data`, so what caching actually did is measurable after the fact
without instrumenting anything and without spending a cent to find out. This
reads those blocks and prints, per workflow and per call site, the cache writes,
the cache reads, and the cost -- which is what "report cost per draft before and
after, on real runs" needs.

It is READ-ONLY. It touches the `n8n` database only, never `novascout`, and it
runs one SELECT.

    python n8n/drafting/cache_report.py                  # last 14 days
    python n8n/drafting/cache_report.py --since 2026-10-08T12:00
    python n8n/drafting/cache_report.py --since 2026-10-08T12:00 --label after

Prices are Claude Sonnet 5.5's, read from the live pricing page on 2026-10-08
and recorded in Section 4. The cache-read rate is the one that is easy to get
wrong: Sonnet 5.5 is a documented exception at 0.05x base input ($0.10/MTok),
not the standard 0.1x -- this document said $0.20 until 2026-10-08.
"""

import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys

# Claude Sonnet 5.5, $/million tokens (Section 4; live pricing page 2026-10-08).
PRICE = {
    "input": 2.00,          # base input
    "write_5m": 2.50,       # 1.25x base
    "write_1h": 4.00,       # 2x base
    "read": 0.10,           # 0.05x base -- the Sonnet 5.5 / Opus 5.5 exception
    "output": 10.00,
}

# The workflows that make Sonnet 5.5 calls, and the node name each call site
# runs under. A call's site is identified by the node whose output holds the
# usage block, which is why this maps node names rather than guessing from the
# token counts.
CALL_SITES = {
    "Claude Draft": "drafting",
    "Claude Claim Check": "claim-check",
    "Claude Repair": "repair",
    "Claude Compose Follow-Up": "follow-up",
    "Claude Reply Draft": "reply-draft",
}

CONTAINER = os.environ.get("NOVASCOUT_PG_CONTAINER", "nova-scout-postgres-1")
PGUSER = os.environ.get("POSTGRES_USER", "novascout")


def psql(db, sql):
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", PGUSER, "-d", db, "-t", "-A", "-F", "\x1f", "-c", sql],
        capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit("psql failed: %s" % (out.stderr or out.stdout).strip())
    return [l for l in out.stdout.splitlines() if l.strip()]


def usage_blocks(data):
    """Every Sonnet 5.5 `usage` object in one execution, attributed to the node
    that made the call.

    n8n stores an execution as a flat, interned array: a dict's values are
    stringified indices into that array, and an identical value is stored once
    and shared. So the usage blocks are reached the same way n8n reaches them --
    resultData.runData, node by node, run by run, item by item -- rather than by
    scanning the array for anything input_tokens-shaped. Walking runData is what
    makes a call attributable: scanning cannot tell a drafting call from a
    claim-check call, and interning means a scan also cannot count two identical
    usage blocks as two calls."""
    try:
        arr = json.loads(data)
    except ValueError:
        return []
    if not isinstance(arr, list) or not arr or not isinstance(arr[0], dict):
        return []

    def deref(v):
        if isinstance(v, str) and v.isdigit():
            i = int(v)
            if 0 <= i < len(arr):
                return arr[i]
        return v

    def num(v):
        v = deref(v)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    try:
        result = deref(arr[0].get("resultData"))
        run_data = deref(result.get("runData")) if isinstance(result, dict) else None
    except (AttributeError, TypeError):
        return []
    if not isinstance(run_data, dict):
        return []

    out = []
    for node_name, runs in run_data.items():
        site = CALL_SITES.get(_base_node(node_name))
        if site is None:
            continue
        for run in (deref(runs) or []):
            run = deref(run)
            if not isinstance(run, dict):
                continue
            branches = deref(run.get("data"))
            branches = deref(branches.get("main")) if isinstance(branches, dict) else None
            for branch in (branches or []):
                for item in (deref(branch) or []):
                    item = deref(item)
                    if not isinstance(item, dict):
                        continue
                    body = deref(item.get("json"))
                    if not isinstance(body, dict):
                        continue
                    u = deref(body.get("usage"))
                    if not isinstance(u, dict) or "input_tokens" not in u:
                        continue
                    cc = deref(u.get("cache_creation"))
                    cc = cc if isinstance(cc, dict) else {}
                    det = deref(u.get("output_tokens_details"))
                    det = det if isinstance(det, dict) else {}
                    out.append({
                        "_node": node_name,
                        "_site": site,
                        "input_tokens": num(u.get("input_tokens")),
                        "cache_creation_input_tokens": num(u.get("cache_creation_input_tokens")),
                        "cache_read_input_tokens": num(u.get("cache_read_input_tokens")),
                        "output_tokens": num(u.get("output_tokens")),
                        "write_5m": num(cc.get("ephemeral_5m_input_tokens")),
                        "write_1h": num(cc.get("ephemeral_1h_input_tokens")),
                        "thinking": num(det.get("thinking_tokens")),
                    })
    return out


def _base_node(name):
    """`Claude Claim Check R1` and `Claude Repair 2` are the unrolled repair
    rounds of the same two call sites (approval_chain.py), so the round suffix
    is dropped before the lookup."""
    for suffix in (" R1", " R2", " R3", " 1", " 2", " 3"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def cost(u):
    """What this one call cost, in dollars."""
    # cache_creation_input_tokens is the total; the per-TTL split says which
    # rate applies. A run with neither split populated predates the split or
    # wrote nothing, so it falls back to the 5-minute rate.
    w5, w1 = u["write_5m"], u["write_1h"]
    if w5 + w1 == 0:
        w5 = u["cache_creation_input_tokens"]
    return (u["input_tokens"] * PRICE["input"]
            + w5 * PRICE["write_5m"]
            + w1 * PRICE["write_1h"]
            + u["cache_read_input_tokens"] * PRICE["read"]
            + u["output_tokens"] * PRICE["output"]) / 1_000_000.0


def uncached_cost(u):
    """What the same call would have cost with no caching at all: every cached
    token charged at the base input rate. This is the honest comparison -- it
    does not need a matching uncached run to exist."""
    total_in = (u["input_tokens"] + u["cache_creation_input_tokens"]
                + u["cache_read_input_tokens"])
    return (total_in * PRICE["input"] + u["output_tokens"] * PRICE["output"]) / 1_000_000.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="ISO timestamp; default 14 days ago")
    ap.add_argument("--until", help="ISO timestamp; default now")
    ap.add_argument("--label", default="", help="a name for this window, printed in the heading")
    ap.add_argument("--per-call", action="store_true", help="print every call, not just the summary")
    args = ap.parse_args()

    since = args.since or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).strftime("%Y-%m-%dT%H:%M")
    until = args.until or "2100-01-01"

    rows = psql("n8n", """
        SELECT e.id, w.name, e."startedAt", d.data
          FROM execution_entity e
          JOIN execution_data d ON d."executionId" = e.id
          JOIN workflow_entity w ON w.id = e."workflowId"
         WHERE e."startedAt" >= '%s' AND e."startedAt" < '%s'
           AND d.data LIKE '%%input_tokens%%'
         ORDER BY e."startedAt"
    """ % (since, until))

    by_site = collections.defaultdict(list)
    for line in rows:
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        eid, name, started, data = parts[0], parts[1], parts[2], "\x1f".join(parts[3:])
        for u in usage_blocks(data):
            u["_execution"] = eid
            u["_started"] = started
            by_site["%s / %s" % (name, u["_site"])].append(u)
    by_wf = by_site

    head = "Prompt-caching report"
    if args.label:
        head += "  [%s]" % args.label
    print("\n%s" % head)
    print("window: %s -> %s" % (since, until))
    print("prices: input $%.2f  write5m $%.2f  write1h $%.2f  read $%.2f  output $%.2f  per MTok"
          % (PRICE["input"], PRICE["write_5m"], PRICE["write_1h"], PRICE["read"], PRICE["output"]))

    if not by_wf:
        print("\nNo Sonnet 5.5 calls in this window.\n")
        return

    g_calls = g_cost = g_unc = 0
    g_read = g_write = 0
    print("\n%-38s %5s %9s %9s %9s %9s %10s %10s %7s"
          % ("workflow / call site", "calls", "in", "write", "read", "out", "cost", "uncached", "saved"))
    print("-" * 120)
    for name in sorted(by_wf):
        us = by_wf[name]
        c = sum(cost(u) for u in us)
        unc = sum(uncached_cost(u) for u in us)
        tin = sum(u["input_tokens"] for u in us)
        tw = sum(u["cache_creation_input_tokens"] for u in us)
        tr = sum(u["cache_read_input_tokens"] for u in us)
        to = sum(u["output_tokens"] for u in us)
        saved = (1 - c / unc) * 100 if unc else 0.0
        print("%-38s %5d %9d %9d %9d %9d %10.4f %10.4f %6.1f%%"
              % (name[:38], len(us), tin, tw, tr, to, c, unc, saved))
        g_calls += len(us); g_cost += c; g_unc += unc; g_read += tr; g_write += tw
        if args.per_call:
            for u in us:
                print("      exec %-7s in=%-6d w=%-6d r=%-6d out=%-6d  $%.5f"
                      % (u["_execution"], u["input_tokens"], u["cache_creation_input_tokens"],
                         u["cache_read_input_tokens"], u["output_tokens"], cost(u)))
    print("-" * 120)
    saved = (1 - g_cost / g_unc) * 100 if g_unc else 0.0
    print("%-38s %5d %9s %9d %9d %9s %10.4f %10.4f %6.1f%%"
          % ("TOTAL", g_calls, "", g_write, g_read, "", g_cost, g_unc, saved))

    print("\ncost per call: $%.5f  (uncached equivalent $%.5f)"
          % (g_cost / g_calls, g_unc / g_calls))
    if g_write == 0 and g_read == 0:
        print("caching is OFF in this window: no writes, no reads.")
    elif g_read == 0:
        print("WARNING: %d tokens written to cache and NOTHING read back. That is the "
              "concurrency failure (Section 9, Workflow 4) or a prefix that differs per call "
              "-- it is a pure %.0f%% write surcharge." % (g_write, (PRICE["write_1h"] / PRICE["input"] - 1) * 100))
    else:
        print("reads/writes = %.2f  (a healthy batch reads once per call after the first)"
              % (g_read / g_write))
    print()


if __name__ == "__main__":
    main()
