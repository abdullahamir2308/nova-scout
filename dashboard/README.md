# Dashboard — is Nova Scout working?

Two read-only views on this PC, both from the same `metrics` views through the same role:

- **The page: http://127.0.0.1:3001** (v1.1) — the design in `design/reference.html` with live numbers,
  refreshing itself every minute. Niche selector: All niches, CRO, Research sites. No login.
- **Grafana: http://127.0.0.1:3000** (v1) — the detailed view, unchanged. Login: user `admin`, password
  `NOVASCOUT_GRAFANA_ADMIN_PASSWORD` in `.env`.

Decisions and definitions: Master Ref §9 "Dashboard"; the schema: §8, migrations `032` and `033`.

## Starting and stopping the page

```text
docker compose up -d dashboard         start (it also starts whenever Docker does: `restart: unless-stopped`)
docker compose stop dashboard          stop (Grafana and everything else keep running)
docker compose restart dashboard       after editing server.py or pgmini.py (the code is mounted read-only; no build)
docker logs nova-scout-dashboard-1     one line per request
```

The page shows "Offline" in its corner (and keeps the last numbers) if the service cannot reach the database.

## What is where

| Path | What |
|---|---|
| `../postgres/migrations/032_reply_outcome.sql` | `inbound_messages.reply_outcome` — the operator's tag on a reply |
| `../postgres/migrations/033_dashboard_metrics.sql` | schema `metrics` (the views), `metrics_private` (constants, wrappers), role `nova_dashboard` |
| `design/reference.html` | the design: the source of truth for how the page looks and moves |
| `web/server.py`, `web/pgmini.py` | the `dashboard` service: serves the page and `/api/data?niche=…`, reading the views as `nova_dashboard` (standard library only; `pgmini.py` is a minimal SCRAM-SHA-256 Postgres client) |
| `web/app.js`, `web/build_page.py` | the page's script, and the build that makes `web/index.html` from it and from the reference (CSS, fonts, `ringSVG`, `gaugeSVG` copied verbatim) |
| `web/test_server.py` | the server and the Postgres client, no database needed (includes the RFC 7677 SCRAM vector) |
| `build_dashboard.py` | generates `grafana/dashboards/nova-scout.json`; refuses on drift (below) |
| `grafana/provisioning/` | the data source (as `nova_dashboard`) and the dashboard provider, both read-only |
| `provision_reader.py` | sets `nova_dashboard`'s password from `NOVASCOUT_DASHBOARD_DB_PASSWORD` (SCRAM, computed locally) |
| `test_drift_guards.py` | the build's guards, each broken once; the committed JSON and page equal their builds |
| `crosscheck.py` | every number, through Grafana *and* through the page's `/api/data`, against a direct SQL query as the superuser |
| `check_reader_role.py` | logs in as `nova_dashboard` the way Grafana does and tries every way out of the views |

## Setting it up (already done on 2026-11-01)

```text
psql < postgres/migrations/032_reply_outcome.sql        (docker exec -i nova-scout-postgres-1 psql -U novascout -d novascout)
psql < postgres/migrations/033_dashboard_metrics.sql
python dashboard/provision_reader.py                      (needs NOVASCOUT_DASHBOARD_DB_PASSWORD in .env)
docker compose up -d grafana                              (needs NOVASCOUT_GRAFANA_ADMIN_PASSWORD in .env)
```

Then a NocoDB metadata sync on the `novascout` source shows `reply_outcome` (a dropdown: positive, neutral,
negative, meeting booked). Tag replies in the `inbound_messages` grid view **Replies — set outcome**.

## Changing it

- **A panel:** edit `build_dashboard.py`, run it, commit both files. Grafana re-reads the JSON within a minute;
  the UI cannot save over it.
- **The page:** edit `web/app.js` (or `web/build_page.py`), run `python dashboard/web/build_page.py`, commit both,
  then `docker compose restart dashboard` (the service reads `index.html` on each request, so a browser reload is
  enough for the page alone). If `design/reference.html` changes, the build refuses where it can no longer find
  what it copies; the CSS in `index.html` is held equal to the reference's by `test_drift_guards.py`.
- **A number on the page:** it is a statement in `web/server.py`. It may only `SELECT` from a `metrics` view
  (`build_dashboard.py` refuses anything else), and `crosscheck.py` needs a matching direct query.
- **A view:** edit `033` and re-apply it (it rebuilds both schemas and re-grants), then run `crosscheck.py`.
- **A new niche:** nothing in Grafana. It is a row in `niches`; it appears in the Niche filter and in every panel.
  The page also shows it (a button, a series, a table column), in a neutral colour until the design gives it one
  (`COLOR` in `app.js`).
- **The Grafana password:** `GF_SECURITY_ADMIN_PASSWORD` is read only when Grafana's own database is created.
  To change it later: `docker exec nova-scout-grafana-1 grafana cli admin reset-admin-password <new>`, then
  update `.env`.
- **The database password:** change `NOVASCOUT_DASHBOARD_DB_PASSWORD`, run `provision_reader.py`, then
  `docker compose restart grafana`.

## Checking it

```text
python dashboard/test_drift_guards.py     # no database needed (one test reads the live schema if up)
python dashboard/web/test_server.py       # no database needed
python dashboard/crosscheck.py            # needs the stack, Grafana and the page up; read-only
python dashboard/crosscheck.py page       # only the page's numbers (or: grafana)
python dashboard/check_reader_role.py     # needs the stack; every probe fails or reads
```

## What the role can still do

Postgres lets every login role read the system catalogs (names and definitions of tables, views and
functions, never their rows), change its own session settings — including turning its read-only default off,
which is why every write probe is repeated in a read-write transaction and must still be refused by
privileges — and change its own password. `033` revokes the one write every role is granted by default
(creating large objects).
