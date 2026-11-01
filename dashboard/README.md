# Dashboard v1 — is Nova Scout working?

One read-only page on this PC: **http://127.0.0.1:3000** (the home page after login). Login: user `admin`,
password `NOVASCOUT_GRAFANA_ADMIN_PASSWORD` in `.env`. Decisions and definitions: Master Ref §9 "Dashboard";
the schema: §8, migrations `032` and `033`.

## What is where

| Path | What |
|---|---|
| `../postgres/migrations/032_reply_outcome.sql` | `inbound_messages.reply_outcome` — the operator's tag on a reply |
| `../postgres/migrations/033_dashboard_metrics.sql` | schema `metrics` (the views), `metrics_private` (constants, wrappers), role `nova_dashboard` |
| `build_dashboard.py` | generates `grafana/dashboards/nova-scout.json`; refuses on drift (below) |
| `grafana/provisioning/` | the data source (as `nova_dashboard`) and the dashboard provider, both read-only |
| `provision_reader.py` | sets `nova_dashboard`'s password from `NOVASCOUT_DASHBOARD_DB_PASSWORD` (SCRAM, computed locally) |
| `test_drift_guards.py` | the build's guards, each broken once; the committed JSON equals the build |
| `crosscheck.py` | every panel's number, through Grafana as `nova_dashboard`, against a direct SQL query as the superuser |
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
- **A view:** edit `033` and re-apply it (it rebuilds both schemas and re-grants), then run `crosscheck.py`.
- **A new niche:** nothing here. It is a row in `niches`; it appears in the Niche filter and in every panel.
- **The Grafana password:** `GF_SECURITY_ADMIN_PASSWORD` is read only when Grafana's own database is created.
  To change it later: `docker exec nova-scout-grafana-1 grafana cli admin reset-admin-password <new>`, then
  update `.env`.
- **The database password:** change `NOVASCOUT_DASHBOARD_DB_PASSWORD`, run `provision_reader.py`, then
  `docker compose restart grafana`.

## Checking it

```text
python dashboard/test_drift_guards.py     # no database needed (one test reads the live schema if up)
python dashboard/crosscheck.py            # needs the stack and Grafana up; read-only
python dashboard/check_reader_role.py     # needs the stack; every probe fails or reads
```

## What the role can still do

Postgres lets every login role read the system catalogs (names and definitions of tables, views and
functions, never their rows), change its own session settings — including turning its read-only default off,
which is why every write probe is repeated in a read-write transaction and must still be refused by
privileges — and change its own password. `033` revokes the one write every role is granted by default
(creating large objects).
