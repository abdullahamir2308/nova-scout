"""Read-only check of the sending domain's mail DNS: SPF, DKIM (key length), DMARC.

    python n8n/sendtrack/check_mail_dns.py                      # selector zmail
    python n8n/sendtrack/check_mail_dns.py --selector zmail2048
    python n8n/sendtrack/check_mail_dns.py --selector zmail --selector zmail2048 --expect-bits 2048

Asks two public resolvers over DNS-over-HTTPS (Google and Cloudflare), so it needs no
`dig` and shows whether a change has reached both. For each DKIM selector it joins the
TXT record's strings (a 2048-bit key is longer than one 255-character DNS string, so
the record is split) and decodes the public key to report its length. For DMARC it
prints the record, checks there is exactly one, and for every rua address on another
domain checks that domain's authorization record (RFC 7489 section 7.1) -- without
it, reporters drop the reports. Master Ref Section 5 has the plan this checks.

Exits 1 if a record is missing, there is not exactly one DMARC record, a selector's key
is not the --expect-bits length, or the two resolvers disagree.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request

RESOLVERS = {
    "google": "https://dns.google/resolve?name=%s&type=TXT",
    "cloudflare": "https://cloudflare-dns.com/dns-query?name=%s&type=TXT",
}


def txt(name):
    """{resolver: [record, ...]} with each record's strings joined."""
    out = {}
    for label, url in RESOLVERS.items():
        req = urllib.request.Request(url % name, headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            answer = json.load(r).get("Answer", [])
        out[label] = sorted("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', a["data"])) or a["data"]
                            for a in answer if a.get("type") == 16)
    return out


def der_len(b, i):
    n = b[i]
    if n < 0x80:
        return n, i + 1
    k = n & 0x7F
    return int.from_bytes(b[i + 1:i + 1 + k], "big"), i + 1 + k


def rsa_bits(p):
    """Modulus length of a DKIM p= value (SubjectPublicKeyInfo, or a bare RSAPublicKey)."""
    b = base64.b64decode(re.sub(r"\s+", "", p))
    i = 0
    assert b[i] == 0x30
    _, i = der_len(b, i + 1)
    if b[i] == 0x30:                       # SubjectPublicKeyInfo: AlgorithmIdentifier, then BIT STRING
        n, j = der_len(b, i + 1)
        i = j + n
        assert b[i] == 0x03
        _, i = der_len(b, i + 1)
        i += 1                             # unused-bits byte
        assert b[i] == 0x30
        _, i = der_len(b, i + 1)
    assert b[i] == 0x02                    # modulus INTEGER
    n, i = der_len(b, i + 1)
    mod = b[i:i + n].lstrip(b"\x00")
    return len(mod) * 8 - (8 - mod[0].bit_length())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", default=(os.environ.get("NOVASCOUT_MAILBOX_ADDRESS", "").split("@")[-1] or "amitrixlabs.com"))
    ap.add_argument("--selector", action="append")
    ap.add_argument("--expect-bits", type=int)
    a = ap.parse_args()
    d, problems = a.domain, []

    def agree(name, got):
        if got["google"] != got["cloudflare"]:
            problems.append("%s: the resolvers disagree (a change still propagating?)" % name)
            print("  google:     %s\n  cloudflare: %s" % (got["google"], got["cloudflare"]))
        return got["google"] or got["cloudflare"]

    print("domain %s" % d)
    spf = [r for r in agree(d, txt(d)) if r.lower().startswith("v=spf1")]
    print("SPF    %s" % (spf or "MISSING"))
    if len(spf) != 1:
        problems.append("expected exactly one SPF record, found %d" % len(spf))

    for sel in a.selector or ["zmail"]:
        name = "%s._domainkey.%s" % (sel, d)
        recs = agree(name, txt(name))
        if not recs:
            print("DKIM   %s: MISSING" % sel)
            problems.append("no DKIM record for selector %s" % sel)
            continue
        p = (re.search(r"(?:^|;)\s*p=([^;]*)", recs[0]) or [None, ""])[1]
        bits = rsa_bits(p) if p.strip() else 0
        print("DKIM   %s: %d-bit RSA, record %d characters" % (sel, bits, len(recs[0])))
        if a.expect_bits and sel == (a.selector or ["zmail"])[-1] and bits != a.expect_bits:
            problems.append("selector %s is %d-bit, expected %d" % (sel, bits, a.expect_bits))

    name = "_dmarc." + d
    dm = [r for r in agree(name, txt(name)) if r.lower().startswith("v=dmarc1")]
    print("DMARC  %s" % (dm or "MISSING"))
    if len(dm) != 1:
        problems.append("expected exactly one DMARC record, found %d (two make both invalid)" % len(dm))
    for rec in dm[:1]:
        rua = (re.search(r"(?:^|;)\s*rua=([^;]*)", rec) or [None, ""])[1]
        for addr in [x.strip()[7:] for x in rua.split(",") if x.strip().lower().startswith("mailto:")]:
            host = addr.split("@")[-1].split("!")[0].lower()
            if host == d or host.endswith("." + d):
                print("  rua %s: on the domain itself -- these reports land in a mailbox here" % addr)
                continue
            auth = "%s._report._dmarc.%s" % (d, host)
            ok = [r for r in agree(auth, txt(auth)) if r.lower().startswith("v=dmarc1")]
            print("  rua %s: external, authorization record %s" % (addr, "present" if ok else "MISSING"))
            if not ok:
                problems.append("%s publishes no %s record; reporters will drop the reports" % (host, auth))

    print("\n" + ("OK" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems)))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
