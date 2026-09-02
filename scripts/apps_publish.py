# -*- coding: utf-8 -*-
"""
EGYM Flow Metrics — APPS realm publish/report/deliver (LDA space)  [bespoke]
==============================================================================
Apps realm equivalent of core_publish.py. Apps' Jira-engine output
(compute_jira.compute_team_values with APPS_TEAMS) already matches
update_data_live.py's "standard realm" schema 1:1 (realm["teams"][tid] =
{fl1:{...}, fl2:{...}}), so this script is a direct, single-shot analogue of
core_publish.py — same engine, same APPS_TEAMS (bma_core_growth/bma_engagement/
trainer/workout), same cadence day 25 (rolled to next business day), same
rolling 120-day window, same validation/anomaly gates. It is adapted only for
the LDA repo's independent data-apps.json (via update_data_live.py's data_path
param) and independent apps/ dashboard pages (via generate_dashboards_live.py's
data_path param), so Apps can run fully independently of Core/Machine/Wellpass.

No changes to data.json/data-core.json/data-machine.json/data-wellpass.json. Apps
has NO DORA page (build_pdf_realm.py's NO_DORA_REALMS already includes "apps" —
nothing to do here for that).

Methodology changes (Apps-scoped only, each with explicit human sign-off):
  - 2026-08-31 (confirmed by Bruno Farace): FL2 (Epics) now excludes any epic
    carrying the Jira label "#ignore_nave" (compute_jira.REALM_FL2_LABEL_EXCLUDE),
    via the realm="apps" argument passed to compute_team_values below. See
    compute_jira.py's REALM_FL2_LABEL_EXCLUDE comment for the full rationale and
    the JQL-empty-labels bug that was caught and fixed during verification.

USAGE (run in sequence on the Apps cadence day):
  # 1. compute + append + push data-apps.json + regenerate apps/ dashboards
  #    (idempotent: skips the recompute+push if the month is already live)
  uv run --with numpy,tzdata python apps_publish.py <jira> <gh> <YYYY-MM-DD> --publish \
      [--force-anomalies]
  # 2. build the report PDF (pass the agent-authored notes file)
  uv run --with numpy,tzdata,reportlab python apps_publish.py <jira> <gh> <YYYY-MM-DD> \
      --report --notes=notes_apps.json --out=/agent/home/Apps_Realm_flow_metrics_YYYY_MM.pdf
  # 3. post to the LDA channel (only after the decision-authority DM/preview is approved,
  #    or immediately for a clean publish with no anomalies — see this realm's own
  #    ExecutionAgent instructions for the exact human-in-the-loop policy)
  uv run --with numpy,tzdata python apps_publish.py <jira> <gh> <YYYY-MM-DD> \
      --deliver --slack=<slack_conn> --pdf=/agent/home/Apps_Realm_flow_metrics_YYYY_MM.pdf

Omit the date to use today (Europe/Madrid). --publish is idempotent: if the month
is already present in data-apps.json it does nothing but still regenerates the
dashboards (safe to re-run). --report/--deliver can be re-run safely too (report
just rebuilds the PDF; deliver renames the manifest to delivery_apps_sent_<month>.json
on success so a re-run reports "nothing pending" instead of double-posting).
"""
import sys, os, json, copy, base64
from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Madrid")
except Exception:
    _TZ = None

import compute_jira as cj
import update_data_live as upd
import generate_dashboards_live as gen
import report_checks as rc
import build_pdf_realm as bp
from agent_tools import call_tool

REALM = "apps"
DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = "data-apps.json"
OWNER = "oleksandrabobina"
REPO = "egym-flow-metrics-lda"
DASH = f"https://{OWNER}.github.io/{REPO}/{REALM}/index.html"
NOTION_REPORT_PAGE = ("https://app.notion.com/p/EGYM-Flow-Metrics-Report-"
                       "1cbd894dd22a805aba70fd917ada71b1"
                       "#3c6d894dd22a800198f8e8c74f6870df")

# Slack delivery — same routing as the live Apps realm (see REALM_APPS.md).
CHANNEL = "C9CBU5S3C"                       # #sw-team-lda
CHANNEL_2 = "C0ALD546AUW"                   # #apps-realm-lead-ldas-space (added 2026-09, additional monthly destination)
CHANNELS = [CHANNEL, CHANNEL_2]
LDA_IDS = ["U09HA43RY6L", "U091T5REN2U"]    # Denys Ambrozhevychius, Bruno Farace
ESCALATE = "UG3UBQWDP"                      # Alexa Bobina

# Methodology-thread note (see REALM_APPS.md "Corrections" section, item 1 — ongoing,
# still relevant to every report). Standard engine includes bugs in throughput and
# defines tech% via customfield_10463 (not 11502). Some old manual reports for Workout
# and the now-retired BMA Enterprise excluded bugs and/or used a different tech
# definition — those old numbers are Corrections, the engine is correct. Evergreen/
# umbrella epics are excluded from FL2 delivered/cycle-time counts via the shared
# FL2_EXCLUDE (no Apps-specific exclusions exist yet — any future one needs the same
# explicit human sign-off process as Core's).
#
# NOTE: the one-time, historical manual-seed-vs-engine data-source switch at the
# Jun->Jul 26 boundary (see REALM_APPS.md "Data provenance") is intentionally NOT
# included here — per the Alexa 2026-07-28 Option-B decision it does not need to be
# repeated on every report, only mentioned if a report or stakeholder question
# specifically touches the Jun->Jul step. Do not add it to this constant without a
# fresh explicit request.
# Rewritten 2026-08-31 (confirmed by Bruno Farace) for reader-facing plain language --
# no internal field/variable names (customfield_10463, FL2_EXCLUDE) in the reader-facing
# report. Same underlying facts as before, just simplified wording.
CORRECTIONS = (
    "Bug counts and tech% now follow one standard Jira-based definition across all teams — "
    "a few older manual reports for Workout and BMA Enterprise used different definitions, so "
    "don't compare those old numbers directly. Epics (FL2) also exclude long-running "
    "\u201cevergreen\u201d epics and any epic labeled #ignore_nave, since these reflect Jira "
    "housekeeping, not real delivery work."
)


def _today(override):
    if override:
        y, m, d = map(int, override.split("-"))
        return date(y, m, d)
    return (datetime.now(_TZ).date() if _TZ else datetime.utcnow().date())


def _labels(today, anchor_override=None, cadence_override=None):
    if anchor_override:
        y, m, d = map(int, anchor_override.split("-"))
        anchor = date(y, m, d)
    else:
        anchor = cj.report_anchor(today.year, today.month, REALM)
    if cadence_override:
        cy, cm = map(int, cadence_override.split("-"))
    else:
        cy, cm = anchor.year, anchor.month
    month = f"{cj.MONTHS_ABBR[cm - 1]} {cy % 100:02d}"
    month_full = date(cy, cm, 1).strftime("%B %Y")
    return anchor, month, month_full, anchor.strftime("%-d %b %Y")


def _arg(argv, name):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(name + "=")), None)


def _write_delivery_manifest(gh_conn, anchor, month, month_full, report_date):
    gen.GH_CONN = gh_conn
    data, _ = gen.load_data(DATA_PATH)
    rd = data["realms"][REALM]

    def snap(fl, i, container):
        return {k: (arr[i] if isinstance(arr, list) and len(arr) >= abs(i) else None)
                for k, arr in container[fl].items()}

    teams_out = {}
    for tid, t in rd["teams"].items():
        teams_out[tid] = {"name": t.get("name", tid),
                           "fl1_now": snap("fl1", -1, t), "fl1_prev": snap("fl1", -2, t),
                           "fl2_now": snap("fl2", -1, t), "fl2_prev": snap("fl2", -2, t)}
    manifest = {
        "realm": REALM, "realm_name": rd["name"], "month_full": month_full,
        "month_key": month, "anchor_iso": anchor.isoformat(), "report_date": report_date,
        "dashboard": DASH, "channel": CHANNEL, "channels": CHANNELS, "ldas": LDA_IDS, "escalate_to": ESCALATE,
        "teams": teams_out,
    }
    path = os.path.join(DIR, f"delivery_{REALM}.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


# ── PUBLISH: compute (single shot — Apps is light, 4 teams) + append + push + dashboards ──
def publish(jira_conn, gh_conn, today, anchor_override=None, cadence_override=None,
            force=False):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    print(f"=== Apps publish ({month}, anchor {anchor}) ===")

    if upd.month_present(gh_conn, REALM, month, data_path=DATA_PATH):
        print(f"--- {month} already present in {DATA_PATH} — idempotent skip (no push). ---")
        print("  (Regenerating dashboards anyway to be safe.)")
        gen.run(gh_conn, [REALM], data_path=DATA_PATH)
        return {"status": "skipped", "month": month}

    print(f"  computing {REALM} {month} (4 teams: bma_core_growth, bma_engagement, "
          f"trainer, workout) ...")
    tv = cj.compute_team_values(jira_conn, anchor, cj.APPS_TEAMS, realm="apps")
    for tk in cj.APPS_TEAMS:
        f1 = tv[tk]["fl1"]; f2 = tv[tk]["fl2"]
        print(f"  {tk:>16}: FL1 ct={f1['ct']} tp={f1['tp']} tech={f1['tech']}% "
              f"wip={f1['wR']}/{f1['wY']}/{f1['wG']} bugs={f1['bC']}/{f1['bR']} | "
              f"FL2 ct={f2['ct']} del={f2['del']} wip={f2['wip']} tech={f2['tech']}%")

    hard = rc.validate(tv)
    if hard:
        print("  === VALIDATION ERRORS ===")
        for e in hard:
            print(f"    ! {e}")
        sys.exit(1)

    if not force:
        try:
            prev = upd.latest_values(gh_conn, REALM, data_path=DATA_PATH)
        except Exception as e:
            prev = None
            print(f"  (prev values load failed: {e})")
        anom = rc.anomalies(prev, tv)
        if anom:
            print(f"  === ANOMALIES (review needed) for {REALM} {month} ===")
            for a in anom:
                print(f"    ? {a}")
            print("  Held for human approval. Re-run with --force-anomalies after approval "
                  "(no data was written).")
            sys.exit(3)

    try:
        res = upd.update(gh_conn, REALM, month, tv, commit_prefix="Data (auto)",
                          data_path=DATA_PATH)
    except upd.UpdateAborted as e:
        print(f"  ABORTED (safety check): {e}")
        sys.exit(1)

    print(f"  update_data: {res['status']} (index {res.get('index')})")
    if res["status"] != "updated":
        print(f"  {REALM} {month} already present — idempotent skip (no push).")
        return {"status": "duplicate", "month": month}

    gen.run(gh_conn, [REALM], data_path=DATA_PATH)
    print(f"  PUBLISHED {REALM} {month} + dashboards regenerated")

    manifest = _write_delivery_manifest(gh_conn, anchor, month, month_full, report_date)
    print(f"  DELIVERY PENDING {REALM} {month} — wrote {os.path.basename(manifest)}; "
          f"author notes_apps.json then run --report and --deliver")
    return {"status": "updated", "month": month}


# ── REPORT: build the PDF (agent-authored notes injected) ──
def build_report(gh_conn, today, notes_path, out_path, anchor_override=None,
                  cadence_override=None):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    gen.GH_CONN = gh_conn
    data, _ = gen.load_data(DATA_PATH)
    rd = data["realms"][REALM]
    if month not in rd.get("months", []):
        print(f" !  {month} not yet in {DATA_PATH} — run --publish first.")
        sys.exit(1)

    notes = None
    if notes_path and os.path.exists(notes_path):
        try:
            notes = json.load(open(notes_path))
        except Exception as e:
            print(f"  WARNING: could not read notes {notes_path}: {e}; building without narrative.")

    rv = copy.deepcopy(rd)
    rv["corrections"] = CORRECTIONS
    if not out_path:
        out_path = os.path.join(DIR, f"Apps_Realm_flow_metrics_{anchor.strftime('%Y_%m')}.pdf")
    bp.build_realm_pdf(rv, REALM, anchor.isoformat(), month_full, out_path, notes=notes)
    print(f"  PDF written: {out_path}")
    return out_path


# ── DELIVER: post to the LDA Slack channel (exact approved template) ──
def deliver(slack_conn, today, pdf_path, anchor_override=None, cadence_override=None):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    if not (pdf_path and os.path.exists(pdf_path)):
        print(f"  ! PDF not found: {pdf_path}")
        sys.exit(1)
    tags = " ".join(f"<@{u}>" for u in LDA_IDS)
    msg = (f"\U0001F4CA *Apps Realm \u2014 Flow Metrics \u00b7 {month_full}*\n"
           f"The {month_full} flow metrics report for the *Apps Realm* is ready.\n"
           f"\U0001F4C4 PDF attached \u00b7 \U0001F4C8 Live dashboard: <{DASH}|Apps Realm dashboard>\n\n"
           f"{tags} \u2014 please review. If everything looks good, please upload the pdf to the "
           f"<{NOTION_REPORT_PAGE}|Notion page>, and share onward with your "
           f"Head of Realm both \u2013 the PDF and the link to the dashboard. If anything looks off, "
           f"please reach out to <https://app.dataleap.ai/agents/a_czovrnggcqhn8vh32dpi|the Agent>.\n\n"
           f"_Automatically generated \u00b7 rolling 120-day window (through {report_date})_")
    for ch in CHANNELS:
        call_tool("slack_post_message", {
            "connectionId": slack_conn, "channelId": ch, "message": msg,
            "attachments": [{"sourcePath": pdf_path, "fileName": os.path.basename(pdf_path),
                              "mimeType": "application/pdf"}],
        })
        print(f"  DELIVERED Apps {month_full} -> Slack {ch}")
    man_path = os.path.join(DIR, f"delivery_{REALM}.json")
    sent_path = os.path.join(DIR, f"delivery_{REALM}_sent_{month.replace(' ', '_')}.json")
    try:
        os.replace(man_path, sent_path)
    except Exception:
        pass


def main():
    argv = sys.argv[1:]
    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) < 2:
        print("usage: apps_publish.py <jira_conn> <gh_conn> [YYYY-MM-DD] "
              "[--publish [--force-anomalies] | --report --notes=.. --out=.. | "
              "--deliver --slack=.. --pdf=..]")
        sys.exit(2)
    jira_conn, gh_conn = pos[0], pos[1]
    today = _today(pos[2] if len(pos) > 2 and pos[2] else None)
    ao = _arg(argv, "--anchor")
    co = _arg(argv, "--cadence")
    force = "--force-anomalies" in argv

    if "--publish" in argv:
        publish(jira_conn, gh_conn, today, ao, co, force)
    elif "--report" in argv:
        build_report(gh_conn, today, _arg(argv, "--notes"), _arg(argv, "--out"), ao, co)
    elif "--deliver" in argv:
        deliver(_arg(argv, "--slack"), today, _arg(argv, "--pdf"), ao, co)
    else:
        print("Nothing to do: pass --publish, --report, or --deliver.")
        sys.exit(2)


if __name__ == "__main__":
    main()
