# EGYM DORA Metrics Dashboard & Automated Pipeline

An end-to-end, dynamic DORA metrics ingestion, wrangling, and publishing pipeline. This system pulls deployment telemetry from Jira and GitHub SRE Inventory, pairs it with Incident.io severity and duration tracking, and dynamically generates HTML dashboards aggregated across all organizational realms.

---

## 🏗️ Architecture & Directory Structure

```text
DORA-Metrics/
├── .github/workflows/       # Automated GitHub Actions monthly pipeline
│   └── dora_monthly_pipeline.yml
├── database/                # SQLite storage and seeding scripts
│   ├── dora_metrics.db      # Consolidated local SQLite database
│   ├── init_db.py           # Schema initialization script
│   ├── schema.sql           # Database tables & views schema
│   └── seed_from_sheet.py   # Google Sheet registry ingestion (gid=0, Jira, GitHub)
├── docs/                    # Output directory for generated HTML dashboards (GitHub Pages)
│   ├── index.html           # Central landing page with dynamic realm cards
│   └── *-dashboard.html     # Realm-specific DORA dashboards
├── ingestion/               # Raw telemetry fetchers
│   ├── github_fetcher.py    # SRE Service Inventory deployment fetcher
│   ├── incident_fetcher.py  # Incident.io ticket & multi-realm duplication engine
│   └── jira_fetcher.py      # Jira release issue fetcher (dynamic JQL)
├── publisher/               # Dashboard generator & templates
│   ├── landing_template.html
│   ├── realm_dashboard_template.html
│   └── site_generator.py    # HTML site publisher & optional Slack notifier
├── wrangling/               # Metric aggregation engines
│   ├── df_cfr_engine.py     # Deployment Frequency & Change Failure Rate engine
│   └── mttd_mttr_engine.py  # Mean/Median MTTD & MTTR duration engine
├── .env                     # Local environment variables & secrets (git-ignored)
├── config.yaml              # Centralized operational configuration
├── README.md                # System documentation
└── requirements.txt         # Python dependency manifest