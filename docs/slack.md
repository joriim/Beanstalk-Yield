# Slack notifications

Run summaries go to **#yield-data-suite**. Messages carry field ids, QA numbers
and links only, never map files, because yield maps belong to the farmer.

Example message:

> ✅ **demo-field-01 / 2026** → L4 ready · class `absolute` · flagged 27% · k combine-a=1.034 · mass check OK · open map

## Option A: incoming webhook (phase 1, no extra dependency)

1. In Slack, create an app → *Incoming Webhooks* → add a webhook for `#yield-data-suite`.
2. Store the URL as an environment variable (never in the repository):
   `export YIELDSUITE_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`
3. `yieldsuite notify-test` then `yieldsuite run field.yaml --notify slack`.

## Option B: bot token

1. Give the app the bot scope `chat:write` (later `files:write`, `commands`).
2. `export YIELDSUITE_SLACK_BOT_TOKEN=xoxb-...` and `pip install yieldsuite[slack]`.
3. `#yield-data-suite` is a private channel: run `/invite @<app name>` in it.

The channel and environment variable names are set in the field-year config
under `notify.slack`.

## Phase 2

- Dagster `dagster-slack` run-failure sensor posting to the same channel.
- Slack Bolt app in Socket Mode: QA cards with buttons such as *Accept k*,
  *Re-run from L2*, and a `/yield status <field> <year>` command.
