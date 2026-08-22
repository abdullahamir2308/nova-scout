# Nova Scout

Automated BD pipeline for Nova Agent Kit — finds, enriches, scores, and drafts
outreach to small-to-medium CROs, delivering a ranked morning review queue.

This repo currently holds the Sprint 0 infrastructure scaffold: no ingestion,
enrichment, scoring, or drafting logic yet.

## Stack

- **n8n** — self-hosted workflow orchestration (Docker)
- **Postgres 16** — single container, two databases: `n8n` (n8n's own state)
  and `novascout` (application schema, built starting Sprint 1)
- **NocoDB** — review UI layer (Docker), default internal metadata store for now
- **Ollama** — native Windows install, NOT containerized (needs direct GPU
  access). Runs and is managed separately from the Docker stack.

## Starting the stack

```powershell
docker compose up -d
```

Ollama must be running natively and separately before any workflow that
calls it — start it from the system tray as usual. It is not part of
`docker compose up -d`.

## URLs

| Service  | URL                     |
|----------|-------------------------|
| n8n      | http://localhost:5678   |
| NocoDB   | http://localhost:8080   |
| Postgres | localhost:5432          |

n8n reaches Ollama at `http://host.docker.internal:11434` — Docker Desktop's
bridge to the host's real network interface.

## Environment

Copy `.env.example` to `.env` and fill in real values. **`.env` is never
committed** — it holds the Postgres credentials and the n8n encryption key.
