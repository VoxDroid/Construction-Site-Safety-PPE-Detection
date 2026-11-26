# Alerts

PPE Guard can post a message to a webhook whenever a violation **opens**. Set it up in
**Settings → Alerts**, then press **Send a test alert**.

| Setting | Meaning |
| --- | --- |
| **Webhook URL** | Where to send alerts. Leave empty to turn alerts off. |
| **Format** | `Slack`, `Discord` or `Generic JSON` (below). |
| **Cooldown per camera** | At most one alert per camera in this many seconds (60 by default). Later violations are still recorded, just not announced. |
| **Dashboard address** | Your dashboard's public address, e.g. `https://ppe.example.com`. When set, alerts link straight to the violation. |

Alerts are sent from a background thread, so a slow or broken webhook never slows the cameras
down. If delivery fails, the Live page's *Cameras live* tile says so.

## Slack

1. Create an app at <https://api.slack.com/apps>, enable **Incoming Webhooks** and add one for the
   channel you want.
2. Paste the `https://hooks.slack.com/services/...` URL and choose **Slack**.

## Discord

1. In the channel's settings, open **Integrations → Webhooks → New Webhook** and copy its URL.
2. Paste it and choose **Discord**.

## Generic JSON

For your own service, Microsoft Power Automate, Home Assistant, n8n, Zapier and similar. The body
is:

```json
{
  "type": "ppe_violation",
  "message": "PPE violation on Riverside Tower / Main gate: worker missing helmet, vest (since 2026-11-26 13:36:02).",
  "id": "8e3da6d2eb4f",
  "kind": "ppe",
  "worker_key": "t14",
  "missing": ["helmet", "vest"],
  "zone_id": null,
  "zone_name": null,
  "started_at": 1795671362.4,
  "last_seen": 1795671362.4,
  "ended_at": null,
  "camera_id": "gate",
  "camera_name": "Main gate"
}
```

For a no-entry zone, `kind` is `"zone"`, `missing` is empty, `zone_id` and `zone_name` are set,
and the message reads *Restricted zone entered on … worker in Trench*. The test alert has
`"test": true`.

The message starts with the **site name** from **Settings → General**. Times are Unix seconds.

## Live events without webhooks

Programs on your network can also listen to the dashboard's WebSocket (`/ws`), which streams every
violation as it opens, updates and closes. See the [API reference](../reference/api.md#websocket).
