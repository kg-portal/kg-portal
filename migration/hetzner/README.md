# CRM Hetzner migration test preparation

Prepared 2026-10-05 from Render's observed main commit
`56c61bd7746a3d995254b5b16ca90adeae6df4f2`.
Branch: `test/crm-hetzner-migration`.

All 97 tracked source files are retained byte for byte. The existing
`kg-ai-mail-test` branch had the same file content as main at inspection.
No application behavior, live service, DNS, database or secret has been changed.
This branch is preparation for a test deployment, not a running Hetzner instance.

## Verified here

Run `python3 migration/hetzner/check_source.py` from this checkout.
It verifies every baseline file by SHA-256 and parses all Python source without
importing the app. Importing app.py would initialize a database and start some
background tasks, so this check intentionally does not import it.
The manifest lists environment variable NAMES only, never their values.
The tracked data/kg_portal.db is a repository copy, NOT the current Render database.

## Required before starting the test instance

- Read Render's actual Python version, build command, start command, environment
  variables, Secret Files, cron services and any other mounted storage. These
  dashboard settings are not fully represented in this repository.
- Check Hetzner RAM, CPU and ports. Use a separate CRM directory, service, database,
  environment file and unused loopback port. Do not reuse /opt/kg-business, its
  database or service. Keep the test accessible by SSH tunnel initially.
- Install requirements.txt in an isolated Python environment. PDF generation uses
  Playwright Chromium and requires its browser/system dependencies and fonts.
  Match the live runtime first; record installed dependency versions.
- The application currently reads **tokenlar.env**, not token.env. Load it before
  importing app.py; some modules read environment variables during import.
  KG_PORTAL_SECRET_KEY and KG_PORTAL_PASSWORD are mandatory at startup.
  Copy all configured Render values, including integration and cron credentials.
  Keep secrets outside version control; do not paste their values into this report.
- Gmail also needs token.json and credentials.json; Calendar needs calendar_key.json.
  Existing code prefers /etc/secrets for these files. Gmail's fallback is static/,
  which is unsuitable for private credentials on a public web service. For the
  isolated service use private, service-specific files and expose them only inside
  that service as /etc/secrets; do not overwrite shared host secrets for Business.
  Calendar alternatively reads data/calendar_key.json. Validate refresh behavior.
- Copy the entire live Render data/ tree, including kg_portal.db, stundenzettel/,
  kg_scan_previews/, payroll documents, worker_whatsapp_numbers.txt and usage files.
  Also inventory runtime files outside data/ (for example google_usage.json).
  Payroll database rows can store absolute file paths: check and map these to the
  new location in the TEST COPY before verifying downloads.
- Use SQLite's backup API for a consistent database snapshot. Do not copy just a
  live .db while ignoring WAL state. For the final migration pause all writers and
  automation before copying the final database and associated files together.
  Record PRAGMA integrity_check, table row counts and file checksums on both ends.
- Do not start a clone with live automation enabled. Existing controls include
  STZ_AUTOMATIK_AUS=1, KAMPAGNEN_BERICHT_LAUF=0, TAGESLISTE_LAUF=0. These controls
  alone do NOT block manual outgoing operations or every integration. Use an
  isolated test network with outbound traffic blocked until explicitly enabling
  a specific integration test. Do not copy/activate live cron schedules yet.

## Acceptance checks (pending on actual copied runtime data)

| Area | Required test |
| --- | --- |
| Login / Portal | Login, sessions, embedded CRM, all navigation and static assets |
| Customers / contracts | Customer counts, details, order, offers, contracts, PDF/DOCX output |
| Employees / Stundenzettel | Employee counts, access codes, existing worker links, hours, signatures, month locks, saved files |
| Payroll | Existing payroll rows, encrypted PDF downloads and document paths; no real sends |
| Accounting | Existing income/expense data, Lexware cache, FinTS records; controlled sync later |
| Leads | All database boxes, scores, pools, Tagesliste, deduplication and collector imports |
| Leon | Existing agent connection, campaigns, callbacks, reports; no live calls during initial test |
| AI / mail / calendar | History, drafts, Gmail credentials, Calendar credentials and permissions; explicit test recipients only |
| WhatsApp | Connector URL/token and existing session location; do not start a second connector for the same account |
| Automations | Stundenzettel, daily list, campaign reports, nightly CRM, Leon cron and external schedulers inventoried |
| KG Scan / website | Previews, KG_SCAN_API_TOKEN, website form target and incoming routes |
| Super Program / Business | CRM URL, agent read access, collector destination, cross-portal integrations |
| Operations | TLS, proxy, restart, permissions, Berlin timezone, backups, restore and rollback |

## Cutover gate (not authorized or executed by this preparation)

Keep Render live until the test instance passes acceptance. Inventory all callers
of the old Render URL, including already-issued worker links, bookmarks, Portal,
collector, scanner, website forms, connector and cron services. Plan a stable CRM
domain and continuity for old links before changing destinations.
Enable each scheduled job in exactly one environment after the final data copy.
After new writes on Hetzner, reverting to Render's old database would lose those
writes: rollback must include the latest data. Do not cancel Render prematurely.

Still unavailable here: live Render data and Secret Files, actual runtime/start
settings, complete external scheduler inventory and current Hetzner RAM/CPU usage.
Consequently this preparation does not certify end-to-end migration success.
