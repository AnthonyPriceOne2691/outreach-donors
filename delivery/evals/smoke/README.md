# Durable smoke oracles

List 3–10 repo-level checks (command + expected). Example:

| ID | Command | Expected | Timeout |
|---|---|---|---|
| S1 | `curl -sf localhost:8000/health` | HTTP 200 | 10s |
| S2 | `pytest -q` | exit 0 | 120s |

Optional: `run.sh` loops IDs and exits 1 on first failure.
