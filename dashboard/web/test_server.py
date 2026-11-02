"""Tests for the dashboard page's server and its Postgres client. No database needed.

    python dashboard/web/test_server.py

  * pgmini: the SCRAM-SHA-256 maths against the RFC 7677 example; the login's refusals (cleartext, a missing or
    wrong server proof, a forged nonce) against a scripted fake server, which is also checked never to receive a
    password it should not; result decoding; errors.
  * server: the niche selection (what may reach SQL), the shape of an answer built from a fake connection and
    the statements it ran (one READ ONLY snapshot, only `metrics` views, only validated niche codes), and the
    HTTP rules (Host check, read-only methods, headers, no database detail in an error).

The numbers themselves are not tested here: dashboard/crosscheck.py compares every one with a direct query.
"""
import base64
import json
import os
import socket
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pgmini  # noqa: E402
import server as S  # noqa: E402


# ---------------------------------------------------------------------------------------------------------
# pgmini
# ---------------------------------------------------------------------------------------------------------
def frame(kind, payload=b""):
    return kind + struct.pack("!I", len(payload) + 4) + payload


def recv_n(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf


def recv_startup(sock):
    (n,) = struct.unpack("!I", recv_n(sock, 4))
    return recv_n(sock, n - 4)


def recv_msg(sock):
    kind = recv_n(sock, 1)
    if not kind:
        return None, b""
    (n,) = struct.unpack("!I", recv_n(sock, 4))
    return kind, recv_n(sock, n - 4)


def row_description(cols):
    out = struct.pack("!H", len(cols))
    for name, oid in cols:
        out += name.encode() + b"\0" + struct.pack("!IhIhih", 0, 0, oid, -1, -1, 0)
    return frame(b"T", out)


def data_row(values):
    out = struct.pack("!H", len(values))
    for v in values:
        out += struct.pack("!i", -1) if v is None else struct.pack("!i", len(v.encode())) + v.encode()
    return frame(b"D", out)


class FakeServer:
    """One scripted connection: `script(sock, seen)` talks to the client; `seen` collects what it sent."""

    def __init__(self, script):
        self.client, self.server = socket.socketpair()
        self.seen = []
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(script,), daemon=True)
        self.thread.start()

    def _run(self, script):
        try:
            script(self.server, self.seen)
        except Exception as e:  # noqa: BLE001 -- surfaced by the test
            self.error = e
        finally:
            self.server.close()

    def connect(self, password="pencil"):
        return pgmini.Connection(None, None, "nova_dashboard", password, "novascout", timeout=5, sock=self.client)


def sasl_script(good_signature=True, finish="ok"):
    """SCRAM with the password 'pencil'; finish='ok' sends AuthenticationOk after the proof, 'skip' sends it
    without the proof (a server that never proves it knows the password)."""
    def script(sock, seen):
        recv_startup(sock)
        sock.sendall(frame(b"R", struct.pack("!I", 10) + b"SCRAM-SHA-256\0\0"))
        kind, payload = recv_msg(sock)
        mech, rest = payload.split(b"\0", 1)
        first = rest[4:].decode()                                   # n,,n=,r=<nonce>
        seen.append(("sasl-initial", mech.decode(), first))
        bare = first[3:]
        client_nonce = bare.split("r=", 1)[1]
        server_first = "r=%s%s,s=%s,i=4096" % (client_nonce, "SERVERPART", base64.b64encode(b"saltsalt").decode())
        if finish == "skip":
            sock.sendall(frame(b"R", struct.pack("!I", 0)) + frame(b"Z", b"I"))
            return
        sock.sendall(frame(b"R", struct.pack("!I", 11) + server_first.encode()))
        kind, payload = recv_msg(sock)
        seen.append(("client-final", payload.decode()))
        _, signature = pgmini.scram_final("pencil", bare, client_nonce, server_first)
        if not good_signature:
            signature = base64.b64encode(b"x" * 32).decode()
        sock.sendall(frame(b"R", struct.pack("!I", 12) + ("v=" + signature).encode()))
        sock.sendall(frame(b"R", struct.pack("!I", 0)) + frame(b"Z", b"I"))
        # then serve queries until the client leaves
        while True:
            kind, payload = recv_msg(sock)
            if not kind or kind == b"X":
                return
            sql = payload.rstrip(b"\0").decode()
            seen.append(("query", sql))
            if sql == "BAD":
                sock.sendall(frame(b"E", b"SERROR\0C42501\0Mpermission denied for table leads\0\0") + frame(b"Z", b"I"))
            else:
                sock.sendall(row_description([("n", 23), ("big", 20), ("x", 1700), ("whole", 1700), ("t", 25), ("b", 16),
                                              ("f", 701), ("z", 25)])
                             + data_row(["5", "9000000000", "13.4", "50", "héllo", "t", "0.5", None])
                             + frame(b"C", b"SELECT 1\0") + frame(b"Z", b"I"))
    return script


class SCRAM(unittest.TestCase):
    def test_rfc7677_example(self):
        # RFC 7677 section 3: user "user", password "pencil"
        final, signature = pgmini.scram_final(
            "pencil", "n=user,r=rOprNGfwEbeRWgbNEkqO", "rOprNGfwEbeRWgbNEkqO",
            "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
        self.assertEqual(final, "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
                                "p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
        self.assertEqual(signature, "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")

    def test_a_nonce_that_does_not_extend_ours_is_refused(self):
        with self.assertRaises(pgmini.PGError):
            pgmini.scram_final("pencil", "n=,r=abc", "abc", "r=zzz,s=c2FsdA==,i=4096")

    def test_wrong_password_gives_a_different_proof(self):
        args = ("n=,r=abc", "abc", "r=abcdef,s=c2FsdA==,i=4096")
        self.assertNotEqual(pgmini.scram_final("pencil", *args)[0], pgmini.scram_final("pencil2", *args)[0])


class Login(unittest.TestCase):
    def test_login_and_decoding(self):
        fake = FakeServer(sasl_script())
        conn = fake.connect()
        rows = conn.query("SELECT 1")
        conn.close()
        self.assertEqual(rows, [{"n": 5, "big": 9000000000, "x": 13.4, "whole": 50, "t": "héllo", "b": True,
                                 "f": 0.5, "z": None}])
        self.assertIsInstance(rows[0]["whole"], int)
        self.assertEqual(fake.seen[0][:2], ("sasl-initial", "SCRAM-SHA-256"))
        self.assertTrue(fake.seen[0][2].startswith("n,,n=,r="))

    def test_an_error_response_is_raised_with_its_sqlstate(self):
        conn = FakeServer(sasl_script()).connect()
        with self.assertRaises(pgmini.PGError) as ctx:
            conn.query("BAD")
        self.assertEqual(ctx.exception.sqlstate, "42501")
        self.assertIn("permission denied", str(ctx.exception))
        self.assertEqual(conn.query("SELECT 1")[0]["n"], 5)        # the connection is usable after an error
        conn.close()

    def test_cleartext_password_request_is_refused_and_nothing_is_sent(self):
        def script(sock, seen):
            recv_startup(sock)
            sock.sendall(frame(b"R", struct.pack("!I", 3)))        # AuthenticationCleartextPassword
            kind, payload = recv_msg(sock)
            seen.append((kind, payload))
        fake = FakeServer(script)
        with self.assertRaises(pgmini.PGError):
            fake.connect()
        fake.thread.join(5)
        self.assertEqual(fake.seen, [(None, b"")], "the client must send nothing after refusing")

    def test_md5_request_is_refused(self):
        def script(sock, seen):
            recv_startup(sock)
            sock.sendall(frame(b"R", struct.pack("!I", 5) + b"salt"))
            seen.append(recv_msg(sock))
        fake = FakeServer(script)
        with self.assertRaises(pgmini.PGError):
            fake.connect()
        fake.thread.join(5)
        self.assertEqual(fake.seen, [(None, b"")])

    def test_a_server_that_skips_its_proof_is_refused(self):
        with self.assertRaises(pgmini.PGError) as ctx:
            FakeServer(sasl_script(finish="skip")).connect()
        self.assertIn("proving", str(ctx.exception))

    def test_a_wrong_server_signature_is_refused(self):
        with self.assertRaises(pgmini.PGError) as ctx:
            FakeServer(sasl_script(good_signature=False)).connect()
        self.assertIn("signature", str(ctx.exception))

    def test_no_scram_on_offer_is_refused(self):
        def script(sock, seen):
            recv_startup(sock)
            sock.sendall(frame(b"R", struct.pack("!I", 10) + b"SCRAM-SHA-256-PLUS\0\0"))
        with self.assertRaises(pgmini.PGError):
            FakeServer(script).connect()

    def test_a_server_error_at_login_is_raised(self):
        def script(sock, seen):
            recv_startup(sock)
            sock.sendall(frame(b"E", b"SFATAL\0C28P01\0Mpassword authentication failed\0\0"))
        with self.assertRaises(pgmini.PGError) as ctx:
            FakeServer(script).connect()
        self.assertEqual(ctx.exception.sqlstate, "28P01")


# ---------------------------------------------------------------------------------------------------------
# server: niche selection
# ---------------------------------------------------------------------------------------------------------
LISTED = ["cro", "research_site", "none"]


class Selection(unittest.TestCase):
    def test_all_and_empty_are_every_listed_niche(self):
        for requested in ("all", "", None):
            self.assertEqual(S.resolve_niches(requested, LISTED), LISTED)

    def test_named_niches_come_back_in_the_databases_order_without_repeats(self):
        self.assertEqual(S.resolve_niches("research_site,cro,cro", LISTED), ["cro", "research_site"])
        self.assertEqual(S.resolve_niches("none", LISTED), ["none"])

    def test_an_unlisted_niche_is_refused(self):
        for bad in ("bogus", "cro,bogus", "cro'; DROP TABLE leads;--", "CRO", "all,cro"):
            with self.assertRaises(S.BadRequest):
                S.resolve_niches(bad, LISTED)

    def test_literal_list_only_takes_plain_codes(self):
        self.assertEqual(S.literal_list(["cro", "research_site"]), "'cro', 'research_site'")
        for bad in ("a'b", "a b", "", "A", "a;b", "a--"):
            with self.assertRaises(S.BadRequest):
                S.literal_list([bad])


# ---------------------------------------------------------------------------------------------------------
# server: an answer built from a fake connection
# ---------------------------------------------------------------------------------------------------------
class FakeConn:
    def __init__(self):
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        if sql.startswith(("BEGIN", "ROLLBACK")):
            return []
        if "FROM metrics.niche_list" in sql:
            return [{"niche": "cro", "label": "Small and mid-size CROs"}, {"niche": "research_site", "label": "Sites"},
                    {"niche": "none", "label": "In no niche"}]
        if "FROM metrics.sends_daily" in sql and "is_today" in sql:
            return [{"sent": 4, "cap": 50}]
        if "FROM metrics.sends_daily" in sql:
            return [{"day": d, "niche": n, "n": 1} for d in ("2026-11-02", "2026-11-03") for n in ("cro", "research_site")]
        if "FROM metrics.queue_runway" in sql:
            return [{"approved": 10, "per_day": 2.5, "days_left": None}]
        if "max(brake_rate_pct)" in sql:
            return [{"pct": 0, "bounces": 0, "sends": 0, "brake_at": 10, "window_days": 3, "complaints": 0,
                     "open_pauses": 0, "reasons": "none"}]
        if "spam_complaints_ever" in sql:
            return [{"ever": 0}]
        if "tagged_positive" in sql:
            return [{"total": 1, "positive": 0, "meetings": 0, "untagged": 1}]
        if "FROM metrics.replies_by_type" in sql:
            return [{"type": "reply", "last7": 0, "total": 1, "permanent": 0, "delay": 0}]
        if "FROM metrics.latest_replies" in sql:
            return []
        if "max(sample_needed)" in sql:
            return [{"need": 100}]
        if "FROM metrics.ab_arms" in sql:
            return [{"niche": "cro", "arm": "A", "first_touches": 3, "follow_ups": 1, "replied_leads": 0, "status": "c"},
                    {"niche": "cro", "arm": "B", "first_touches": 2, "follow_ups": 0, "replied_leads": 0, "status": "c"}]
        if "FROM metrics.funnel" in sql:
            return [dict(leads=5, enriched=5, scored=5, contact=2, drafted=2, approved=1, sent=1, disqualified=3)]
        if "FROM metrics.held_by_reason" in sql:
            return [{"cls": "linkedin", "held": 2, "needs": 0}]
        raise AssertionError("an unexpected statement: " + sql)


class BuildData(unittest.TestCase):
    def test_one_read_only_snapshot_of_metrics_views_only(self):
        conn = FakeConn()
        data = S.build_data(conn, "cro")
        self.assertTrue(conn.sql[0].startswith("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        self.assertEqual(conn.sql[-1], "ROLLBACK")
        for sql in conn.sql[1:-1]:
            self.assertRegex(sql, r"^SELECT\b")
            self.assertNotIn("$NICHES", sql)
            self.assertNotRegex(sql, r"\b(?:FROM|JOIN)\s+(?!metrics\.)")
        self.assertEqual(data["selected"], ["cro"])
        self.assertTrue(any("niche IN ('cro')" in s for s in conn.sql))

    def test_the_mailbox_wide_figures_ignore_the_selection(self):
        conn = FakeConn()
        S.build_data(conn, "cro")
        for sql in conn.sql:
            if "brake_rate_pct" in sql:
                self.assertNotIn("niche IN", sql)

    def test_shape(self):
        data = S.build_data(FakeConn(), "all")
        self.assertEqual(data["selected"], LISTED)
        self.assertEqual([o["key"] for o in data["options"]], LISTED)
        self.assertEqual(data["days"][0], {"iso": "2026-11-02", "n": {"cro": 1, "research_site": 1, "none": 0}})
        self.assertEqual(data["today"], {"sent": 4, "cap": 50})
        self.assertEqual(data["queue"], {"approved": 10, "perDay": 2.5, "daysLeft": None})
        self.assertEqual(data["brake"], {"paused": False, "reasons": ""})
        self.assertEqual(data["ab"], [{"niche": "cro", "reached": False,
                                       "arms": [{"a": "A", "n": 3, "fu": 1, "rep": 0}, {"a": "B", "n": 2, "fu": 0, "rep": 0}]}])
        self.assertEqual(data["need"], 100)
        json.dumps(data)                                    # serialisable as is

    def test_an_unlisted_niche_runs_nothing_but_the_list(self):
        conn = FakeConn()
        with self.assertRaises(S.BadRequest):
            S.build_data(conn, "bogus")
        self.assertEqual(len(conn.sql), 3, conn.sql)       # BEGIN, the niche list, ROLLBACK
        self.assertIn("metrics.niche_list", conn.sql[1])

    def test_a_pause_shows_its_reason(self):
        class Paused(FakeConn):
            def query(self, sql):
                if "max(brake_rate_pct)" in sql:
                    return [{"pct": 12.5, "bounces": 4, "sends": 32, "brake_at": 10, "window_days": 3, "complaints": 1,
                             "open_pauses": 1, "reasons": "bounce-rate since 2026-11-03"}]
                return FakeConn.query(self, sql)
        data = S.build_data(Paused(), "all")
        self.assertEqual(data["brake"], {"paused": True, "reasons": "bounce-rate since 2026-11-03"})
        self.assertEqual(data["spam"]["counted"], 1)


# ---------------------------------------------------------------------------------------------------------
# server: HTTP
# ---------------------------------------------------------------------------------------------------------
class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.host = "127.0.0.1:%d" % cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.saved = (S.ALLOWED_HOSTS, S.fetch)
        S.ALLOWED_HOSTS = {cls.host}
        cls.asked = []

        def fetch(requested):
            cls.asked.append(requested)
            if requested == "bogus":
                raise S.BadRequest("unknown niche: 'bogus'")
            if requested == "down":
                raise pgmini.PGError("password authentication failed for user nova_dashboard secret-detail")
            return {"selected": [requested]}
        S.fetch = fetch

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        S.ALLOWED_HOSTS, S.fetch = cls.saved

    def call(self, path, method="GET", host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method)
        req.add_header("Host", host or self.host)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_the_page_and_its_headers(self):
        code, headers, body = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>Nova Scout</title>", body)
        csp = headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotRegex(csp, r"https?:|\*")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_another_host_name_is_refused(self):
        for path in ("/", "/api/data", "/healthz"):
            code, _, _ = self.call(path, host="evil.example:%d" % self.port)
            self.assertEqual(code, 421, path)

    def test_only_reading(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            code, _, _ = self.call("/api/data", method=method)
            self.assertEqual(code, 405, method)

    def test_routes_and_parameters(self):
        self.assertEqual(self.call("/nope")[0], 404)
        self.assertEqual(self.call("/healthz")[:1], (200,))
        self.assertEqual(self.call("/api/data?niche=a&niche=b")[0], 400)
        self.assertEqual(self.call("/api/data?other=1")[0], 400)
        self.assertEqual(self.call("/api/data?niche=bogus")[0], 400)
        self.asked.clear()
        self.assertEqual(json.loads(self.call("/api/data")[2]), {"selected": ["all"]})
        self.assertEqual(json.loads(self.call("/api/data?niche=cro,research_site")[2]), {"selected": ["cro,research_site"]})
        self.assertEqual(self.asked, ["all", "cro,research_site"])

    def test_a_database_failure_is_a_502_without_its_detail(self):
        code, _, body = self.call("/api/data?niche=down")
        self.assertEqual(code, 502)
        self.assertNotIn(b"secret-detail", body)
        self.assertNotIn(b"password", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
