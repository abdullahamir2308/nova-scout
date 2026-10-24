"""Proves every rule of the chat-widget detection fails a test when it is broken.

    python n8n/enrichment/test_chatbot_mutations.py

Each mutation breaks one rule in code_fetch.js -- most put back a pattern that
matched a vendor's domain rather than its chat embed (the 2026-10-24 false
positive: js.hs-scripts.com and hs-banner disqualified 15 of 17 leads as "HubSpot
Chat") -- and test_chatbot_detect.js must then fail. Exits non-zero if any
mutation survives, or if the suite fails unmutated.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "code_fetch.js")
TEST = os.path.join(HERE, "test_chatbot_detect.js")

# (label, old, new) -- old must occur exactly once in code_fetch.js.
MUTATIONS = [
    ("HubSpot: the tracking loader counts as chat again",
     r"['HubSpot Chat', /js(?:-[a-z]{2,3}\d)?\.usemessages\.com|",
     r"['HubSpot Chat', /js\.hs-scripts\.com|js(?:-[a-z]{2,3}\d)?\.usemessages\.com|"),
    ("HubSpot: the cookie banner counts as chat again",
     r"|hubspot-messages-iframe-container/i]", r"|hubspot-messages-iframe-container|hs-banner/i]"),
    ("HubSpot: the direct chat embed is not recognised",
     r"['HubSpot Chat', /js(?:-[a-z]{2,3}\d)?\.usemessages\.com|conversations-embed\.js|hubspot-messages-iframe-container/i]",
     r"['HubSpot Chat', /(?!)/i]"),
    ("Intercom: any intercomcdn.com asset counts", r"js\.intercomcdn\.com|intercomSettings", r"intercomcdn\.com|intercomSettings"),
    ("Tawk: any mention of tawk.to counts", r"/embed\.tawk\.to|Tawk_API/i", r"/embed\.tawk\.to|tawk\.to/i"),
    ("Crisp: any crisp.chat link counts", r"/client\.crisp\.chat|CRISP_WEBSITE_ID/i", r"/client\.crisp\.chat|crisp\.chat/i"),
    ("LiveChat: any livechatinc.com URL counts", r"/cdn\.livechatinc\.com|__lc\.license/i", r"/cdn\.livechatinc\.com|livechatinc\.com/i"),
    ("Zendesk: any static.zdassets.com asset counts", r"static\.zdassets\.com\/ekr\/snippet\.js|", r"static\.zdassets\.com|"),
    ("the portal loader is never fetched", "const loaders = await hubspotLoaderChat(blob);", "const loaders = [];"),
    ("a loader that answers counts as chat whatever it holds",
     "chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : gone ? false : null",
     "chat: read ? true : gone ? false : null"),
    ("the loader is asked for with the page's HTML Accept header (HubSpot: 406)",
     "await get(url, { accept: LOADER_ACCEPT });", "await get(url);"),
    ("an unreadable answer (503) is called 'no chat' instead of unknown",
     "chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : gone ? false : null",
     "chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : false"),
    ("a gone portal (410) is called unknown instead of 'no chat'",
     "chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : gone ? false : null",
     "chat: read ? HUBSPOT_CHAT_IN_LOADER.test(body) : null"),
    ("the loader is read for the wrong thing", r"const HUBSPOT_CHAT_IN_LOADER = /usemessages\.com\/conversations-embed/i;",
     r"const HUBSPOT_CHAT_IN_LOADER = /hs-analytics\.net/i;"),
    ("a loader that cannot be fetched counts as chat", "status: null, chat: null,", "status: null, chat: true,"),
    ("regional loader hosts (js-na2., js-eu1.) are ignored",
     r"const HUBSPOT_LOADER_RE = /\/\/(js(?:-[a-z]{2,3}\d)?\.hs-scripts\.com)\/(\d+)\.js/gi;",
     r"const HUBSPOT_LOADER_RE = /\/\/(js\.hs-scripts\.com)\/(\d+)\.js/gi;"),
    ("the same portal is fetched once per reference", "    if (seen.has(m[2])) continue;\n", ""),
    ("no cap on loaders fetched per lead", " && out.length < MAX_HUBSPOT_LOADERS", ""),
    ("a loader is fetched even when the page already embeds chat", "  if (!evidence['HubSpot Chat']) {", "  if (true) {"),
    ("the evidence is not kept", "    if (hit) evidence[vendor] = hit;", "    if (hit) evidence[vendor] = ' ';"),
]


def run(path):
    env = dict(os.environ, NOVASCOUT_CODE_FETCH=path)
    r = subprocess.run(["node", TEST], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def main():
    src = open(SRC, encoding="utf-8").read()
    code, out = run(SRC)
    if code != 0:
        print(out)
        raise SystemExit("the suite fails on the unmutated code_fetch.js")
    survived = 0
    tmp = tempfile.mkdtemp(prefix="novascout-chat-mut-")
    for label, old, new in MUTATIONS:
        n = src.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its text occurs %d times in code_fetch.js, expected once" % (label, n))
        path = os.path.join(tmp, "code_fetch.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new))
        code, out = run(path)
        fails = [l for l in out.splitlines() if l.startswith("FAIL")]
        if code == 0:
            survived += 1
            print("SURVIVED  %s" % label)
        else:
            print("killed    %s  (%s)" % (label, fails[0][6:70] if fails else out.strip().splitlines()[-1][:70]))
    print("\n%d mutations, %d killed, %d survived" % (len(MUTATIONS), len(MUTATIONS) - survived, survived))
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
