# Browser UI

```bash
yieldsuite ui fields/                 # every */field.yaml under the folder
yieldsuite ui field.yaml --port 8765  # open http://127.0.0.1:8765/
```

A small local web app on the same core as the command line (standard library only, no
extra packages). One page, Finnish or English:

- **Field list** with one dot per product (yield level, pattern, filled area, moisture,
  protein): red = low, orange = medium, green = high certainty, and the number of open
  questions.
- **Summary** (for the farmer): mean yield, total, moisture, protein, filled share; what to
  trust and why; the open questions, each with the form that takes the answer where the
  chain can use it directly: scale ticket, antenna position, sowing date, prediction model
  (with the cross-validated error of every model). Other questions show the config key.
- **Maps**: the report figures, links to the report, the 3D viewer, the gap-fill story and
  the videos; the 3D viewer can be shown inside the page.
- **Inputs** (for people working with the data): machines, tickets, model and sowing date,
  the full table of known / estimated / assumed / missing items, and the change log.
- **Run**: run the chain and the report again (optionally the visualisations) in the
  background, with the log.
- **For developers**: the machine-readable files and the HTTP API the page itself uses.

Changes are written to `field.yaml` and `scale_tickets.csv` exactly as by `yieldsuite machine
set`, `tickets add` and logged in `inputs_log.jsonl` (actor `ui`). They take effect on the
next run.

Safety: the server listens on 127.0.0.1 by default and has no accounts; it is for one
person on their own machine. Every change needs a token that only the loaded page has, so
another web page cannot post to it. Files are served from the fields' output folders only.
`--host 0.0.0.0` exposes it to the network: do that only on a network you trust.

API: `GET /api/fields`, `GET /api/field/<key>`, `GET /files/<key>/<path>`,
`POST /api/field/<key>/{ticket|machine|setting|run}` with header `X-Token`.
