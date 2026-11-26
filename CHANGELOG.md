# Changelog

## 2.0.1

Fixes from a full review of the dashboard, the website and the demo: every page in both themes
on desktop, tablet and phone, plus edge cases such as very long names and hostile input.

### Fixed
- **Video analysis**: a violation still open when an uploaded video ended was given the
  wall-clock time as its end, so it showed as lasting hundreds of thousands of hours.
- **CSV export**: notes and names starting with `=`, `+`, `-` or `@` are now written so that
  spreadsheet apps show them as text instead of running them as formulas.
- **Dialogs** closed when you leave their page (back button, a link, an address typed in). Before,
  a violation's detail could stay open over the next page and block it.
- **Names** made only of spaces are refused, and spaces around site, camera, source and zone
  names are trimmed. The forms say so before sending.
- **Phone layouts**: analytics, reports and analyze no longer run wider than the screen; report
  tables scroll sideways; the ACTION stamp no longer covers the KPI label on narrow tiles.
- **Long names and notes** (one long word, a URL) wrap or are shortened everywhere instead of
  widening the page, including camera filters, the status bar, breadcrumbs and reports.
- **Clips on Windows** use the built-in Media Foundation H.264 encoder directly, so the log no
  longer shows two OpenCV errors for every clip.
- **Website**: the landing page has a light (vellum) version that follows the theme switch; the
  caution tapes scroll in a seamless loop at any screen width; the headline no longer runs into
  the hard hat on very wide screens; the keyboard "skip to content" link works.
- **Demo**: has the light version too, follows the theme chosen on the docs site, and has its
  own theme switch that the docs site remembers.
- Zone names with `&` are shown correctly on the zone editor's image.
- The zone editor shows a clear message, not a broken image, when a camera gives no frame.
- **Reports**: only the Note column is in red handwriting; compliance figures and counts in
  the last column of other tables were styled as notes.

## 2.0.0

A rewrite that turns the training project into a complete site-monitoring tool. The repository
was renamed from `Construction-Site-Safety-PPE-Detection` to `ppe-guard`; old links redirect.

### Added
- **`ppe-guard` Python package and CLI**: `serve`, `password`, `predict`, `train`, `val`,
  `export`, `dataset download`, `weights list/download`, `doctor`.
- **Per-worker compliance.** PPE boxes are matched to the person wearing them using overlap plus
  body-region checks, and people are followed across frames with ByteTrack. v1 judged the whole
  frame, so one worker's hard hat could make another worker look compliant.
- **Temporal smoothing.** A violation opens only after an item has looked missing for most of
  `violation_delay` seconds, and closes after `clear_after` seconds of compliance or when the
  worker leaves the frame. **Strict mode** treats unseen items as missing.
- **Zones** per camera, drawn in a zone editor: PPE zones (PPE is only checked inside them, with
  optional per-zone required items) and no-entry zones that raise a violation when someone stays
  inside.
- **Video clips** of each violation, starting a few seconds before it, alongside snapshots.
- **Compliance rate** based on monitored worker-time, recorded per camera and hour.
- **Analytics page**: violations per day, compliance trend, weekday-by-hour heatmap, per item,
  per camera and per zone.
- **Printable daily and weekly reports** (print or save as PDF).
- **Analyze page**: upload a photo or a video and get an annotated result with a per-worker
  verdict, or a violation timeline for videos.
- **Optional password login** with session cookies, an API token for scripts, and login rate
  limiting. `PPE_GUARD_PASSWORD` for containers.
- **Any video source**: webcams, video files (optionally looped) and RTSP/HTTP streams, many
  cameras at once, with automatic reconnect.
- **A visual identity of its own** ("the drawing set"): blueprint and vellum themes, stencil type,
  title-block KPIs, rubber-stamp statuses and red-pen markup, used by the dashboard, the website and the demo.
- **New dashboard**: live view with per-worker status, camera pages, violations with filters,
  bulk review and a detail view with clip and snapshots, settings for everything, light and dark
  themes and a phone layout. No build step.
- **Alerts**: webhooks in generic JSON, Slack or Discord format, with a per-camera cooldown, and
  a WebSocket feed of live events.
- **Data retention** setting, site name, CSV export, and a full REST API with OpenAPI docs.
- **YOLO26s model** (new default, test mAP50-95 0.574 against 0.556 for the v1 YOLOv8s model),
  trained on the same dataset. Both models and an ONNX export are published as release assets
  and download on first use.
- **Documentation site** with guides, deployment, configuration, CLI and API reference, and an
  **in-browser demo** that runs the model with ONNX Runtime Web.
- Docker image and compose file (CPU or NVIDIA GPU), GitHub Actions CI (unit, API and browser
  tests, Docker smoke test, docs build), a stress-test script, issue templates and a contributor
  guide.

### Fixed
- Multi-person mode never flagged missing vests (it looked for `no-vest`, but the class is
  `NO-Safety Vest`).
- SQL injection through the `sort` query parameter.
- `/download_snapshot` could read any file on disk via absolute paths.
- The Flask app ran with `debug=True` and a hard-coded secret key.
- The dashboard loaded the 80-epoch weights while the docs described the 200-epoch model.
- Violations left "open" after a crash are closed on the next start.

### Changed
- The dataset, sample videos and model weights are no longer stored in the repository.
  Download them with `ppe-guard dataset download` and `ppe-guard weights download`.
- The README and docs were rewritten: metrics are re-measured, including test-split results, and
  claims about features that did not exist were removed.
- `Model-Training/`, `Model-Deployment/` and `Model-Testing/` were replaced by `src/ppe_guard/`
  and `training/`.

## 1.0.0

- YOLOv8s fine-tuned on the Construction Site Safety dataset, a training notebook and a Flask
  dashboard with webcam monitoring.
