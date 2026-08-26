# DORA Metrics Solution

Automated DORA (DevOps Research and Assessment) metrics ingestion, analysis, and reporting pipeline.

**Architecture**
* **`ingestion/`**: Fetches deployments from GitHub (`github_fetcher.py`), incidents (`incident_fetcher.py`), and Jira releases (`jira_fetcher.py`).
* **`wrangling/`**: Calculates Deployment Frequency (DF), Change Failure Rate (CFR), MTTD, and MTTR (`df_cfr_engine.py`, `mttd_mttr_engine.py`).
* **`publisher/`**: Generates interactive HTML dashboards (`site_generator.py`).
* **`docs/`**: Output directory deployed to GitHub Pages.
* **`.github/workflows/`**: CI/CD automation (`dora_monthly_pipeline.yml`).

**Environment Setup**
Create a local `.env` file in the project root:

```env
SERVICE_INVENTORY_URL=https://your-internal-inventory-url/api/services
SERVICE_INVENTORY_TOKEN=your_inventory_token
JIRA_URL=[https://your-domain.atlassian.net](https://your-domain.atlassian.net)
JIRA_USER_EMAIL=your_email@company.com
JIRA_API_KEY=your_jira_api_key
INCIDENT_IO_API_KEY=your_incident_io_api_key
SLACK_WEBHOOK_URL_ENGINEERING=your_slack_webhook_url_1
SLACK_WEBHOOK_URL_EXECUTIVE=your_slack_webhook_url_2
**Local Setup & Execution**

1. Install dependencies:
   ```bash
   pip install -r requirements.txt