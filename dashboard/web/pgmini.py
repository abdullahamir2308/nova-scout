"""A minimal PostgreSQL client for the dashboard page: simple query protocol, SCRAM-SHA-256, standard library only.

Why this exists. The page's server runs in the same kind of container as the imap-health and smtp-send sidecars
(the pinned python:alpine image, code mounted read-only, nothing installed), and that image has no Postgres
driver. Rather than build an image around a pip package, this file speaks the one small part of the protocol the
page needs: log in as a role with a SCRAM-SHA-256 password, send plain SELECT texts, read text results back.

What it will not do: any other authentication (cleartext and md5 are refused even if the server asks, so a
fake server cannot extract the password), TLS (the connection is on the compose network, like Grafana's),
prepared statements, COPY. The server's signature is verified, so a server that does not know the password's
verifier cannot pass for Postgres. The password is used as-is (no SASLprep); the generated one is ASCII.

scram_final() is pinned to the RFC 7677 test vector in test_server.py.
"""
import base64
import hashlib
import hmac
import os
import socket
import struct


class PGError(Exception):
    def __init__(self, message, sqlstate=None):
        Exception.__init__(self, message)
        self.sqlstate = sqlstate


def _hmac(key, msg):
    return hmac.new(key, msg, hashlib.sha256).digest()


def scram_final(password, client_first_bare, client_nonce, server_first):
    """RFC 5802 / 7677 client-final-message and the server signature it must be answered with.

    -> (client_final_message, expected_server_signature_b64). Raises PGError when the server's nonce does not
    extend ours (a replayed or forged challenge)."""
    attrs = dict(p.split("=", 1) for p in server_first.split(","))
    nonce, salt, iterations = attrs["r"], base64.b64decode(attrs["s"]), int(attrs["i"])
    if not nonce.startswith(client_nonce):
        raise PGError("SCRAM: the server's nonce does not extend the client's")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = _hmac(salted, b"Client Key")
    stored_key = hashlib.sha256(client_key).digest()
    without_proof = "c=biws,r=" + nonce
    auth_message = (client_first_bare + "," + server_first + "," + without_proof).encode("utf-8")
    signature = _hmac(stored_key, auth_message)
    proof = bytes(a ^ b for a, b in zip(client_key, signature))
    server_signature = _hmac(_hmac(salted, b"Server Key"), auth_message)
    return (without_proof + ",p=" + base64.b64encode(proof).decode(),
            base64.b64encode(server_signature).decode())


def _convert(oid, text):
    if text is None:
        return None
    if oid in (20, 21, 23, 26):          # int8 int2 int4 oid
        return int(text)
    if oid in (700, 701):                # float4 float8
        return float(text)
    if oid == 1700:                      # numeric
        return float(text) if "." in text else int(text)
    if oid == 16:                        # bool
        return text == "t"
    return text


class Connection:
    def __init__(self, host, port, user, password, database, timeout=20, application="nova-dashboard-page", sock=None):
        self.sock = sock or socket.create_connection((host, port), timeout)      # `sock`: for the tests' fake server
        self.sock.settimeout(timeout)
        try:
            self._login(user, password, database, application)
        except BaseException:
            self.sock.close()
            raise

    # -- wire ---------------------------------------------------------------------------------------------
    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PGError("the server closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        kind = self._read(1)
        (length,) = struct.unpack("!I", self._read(4))
        return kind, self._read(length - 4)

    def _send(self, kind, payload=b""):
        self.sock.sendall(kind + struct.pack("!I", len(payload) + 4) + payload)

    @staticmethod
    def _error(payload):
        fields = {}
        for part in payload.split(b"\0"):
            if part:
                fields[part[:1].decode()] = part[1:].decode("utf-8", "replace")
        return PGError(fields.get("M", "server error"), fields.get("C"))

    # -- login --------------------------------------------------------------------------------------------
    def _login(self, user, password, database, application):
        params = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in (
            ("user", user), ("database", database), ("client_encoding", "UTF8"), ("application_name", application)))
        body = struct.pack("!I", 196608) + params + b"\0"
        self.sock.sendall(struct.pack("!I", len(body) + 4) + body)
        client_nonce = base64.b64encode(os.urandom(18)).decode()
        first_bare = "n=,r=" + client_nonce
        expected = None
        pending = False        # SASL started and the server's signature not yet checked
        while True:
            kind, payload = self._recv()
            if kind == b"E":
                raise self._error(payload)
            if kind == b"R":
                (code,) = struct.unpack("!I", payload[:4])
                if code == 0:                                   # AuthenticationOk
                    if pending:
                        raise PGError("login finished without the server proving it knows the password")
                elif code == 10:                                # SASL: choose SCRAM-SHA-256 or refuse
                    if b"SCRAM-SHA-256\0" not in payload[4:]:
                        raise PGError("the server offers no SCRAM-SHA-256")
                    first = ("n,," + first_bare).encode()
                    self._send(b"p", b"SCRAM-SHA-256\0" + struct.pack("!i", len(first)) + first)
                    pending = True
                elif code == 11:                                # SASLContinue
                    final, expected = scram_final(password, first_bare, client_nonce, payload[4:].decode())
                    self._send(b"p", final.encode())
                elif code == 12:                                # SASLFinal
                    got = dict(p.split("=", 1) for p in payload[4:].decode().split(","))
                    if not expected or not hmac.compare_digest(got.get("v", ""), expected):
                        raise PGError("the server's SCRAM signature is wrong")
                    pending = False
                else:
                    raise PGError("the server asked for an authentication this client refuses (code %d)" % code)
            elif kind == b"Z":                                  # ReadyForQuery
                return
            # S (parameter status), K (backend key), N (notice): nothing to do

    # -- queries ------------------------------------------------------------------------------------------
    def query(self, sql):
        """One statement -> a list of dicts keyed by column name (the last result set if the text holds several)."""
        self._send(b"Q", sql.encode("utf-8") + b"\0")
        names, oids, rows, error = [], [], [], None
        while True:
            kind, payload = self._recv()
            if kind == b"T":
                (count,) = struct.unpack("!H", payload[:2])
                pos, names, oids, rows = 2, [], [], []
                for _ in range(count):
                    end = payload.index(b"\0", pos)
                    names.append(payload[pos:end].decode())
                    oids.append(struct.unpack("!I", payload[end + 7:end + 11])[0])
                    pos = end + 19
            elif kind == b"D":
                (count,) = struct.unpack("!H", payload[:2])
                pos, row = 2, {}
                for i in range(count):
                    (n,) = struct.unpack("!i", payload[pos:pos + 4])
                    pos += 4
                    text = None
                    if n >= 0:
                        text = payload[pos:pos + n].decode("utf-8")
                        pos += n
                    row[names[i]] = _convert(oids[i], text)
                rows.append(row)
            elif kind == b"E":
                error = self._error(payload)
            elif kind == b"Z":
                if error:
                    raise error
                return rows

    def close(self):
        try:
            self._send(b"X")
        except OSError:
            pass
        self.sock.close()
