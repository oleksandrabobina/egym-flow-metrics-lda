# -*- coding: utf-8 -*-
"""
EGYM Flow Metrics — MACHINE reliable auto-publish path (controlled append).  [reusable]
================================================================================
WHY THIS EXISTS:
  The live data-machine.json Machine realm is stored in the "flat" Variant-B shape: 8 FL1
  task-teams under realm["teams"], each carrying BOTH fl1 AND fl2 arrays, and NO
  realm["epic_cards"] key. The standard `machine_monthly.py --publish` path calls
  update_data_live.update(), which iterates the *engine* keys and expects an
  epic_cards container — so against the live flat structure it would write an
  all-null month and drop FL2. DO NOT use `machine_monthly.py --publish` on live.

  This module is the safe replacement: it takes the batch-computed staging files
  (produced by `machine_monthly.py --part=...`, engine keys), maps them onto the
  live team keys, appends the new month to a FRESHLY fetched live data-machine.json,
  pushes, regenerates the dashboards, and builds the Variant-B report PDF.

  Everything here is DETERMINISTIC and IDEMPOTENT except the per-month narrative
  `notes` (which the ExecutionAgent authors from the freshly-computed numbers and
  passes in as a JSON file). Structure, key mapping, epic-card VIEW, corrections
  text and the Slack message template are all fixed in code.

USAGE (run in sequence on the Machine cadence day, after the 4 compute parts):
  # data + dashboards (idempotent; skips if the month is already live)
  uv run --with numpy,tzdata python machine_publish.py <jira> <gh> <YYYY-MM-DD> --publish
  # build the report PDF (pass the agent-authored notes file)
  uv run --with numpy,tzdata,reportlab python machine_publish.py <jira> <gh> <YYYY-MM-DD> \
      --report --notes=notes_machine.json --out=/agent/home/Machine_Realm_flow_metrics_YYYY_MM.pdf
  # post to the LDA channel (only after Alexa approves the DM)
  uv run --with numpy,tzdata python machine_publish.py <jira> <gh> <YYYY-MM-DD> \
      --deliver --slack=<slack_conn> --pdf=/agent/home/Machine_Realm_flow_metrics_YYYY_MM.pdf

Omit the date to use today (Europe/Madrid). --publish is idempotent: if the month
is already present in live data-machine.json it does nothing (safe to re-run).
"""
import sys, os, json, copy, base64, collections
from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Madrid")
except Exception:
    _TZ = None

import compute_jira as cj
import generate_dashboards_live as gen
import build_pdf_realm as bp
from agent_tools import call_tool

REALM  = "machine"
DIR    = os.path.dirname(os.path.abspath(__file__))
OWNER  = "oleksandrabobina"
REPO   = "egym-flow-metrics-lda"
DASH   = f"https://{OWNER}.github.io/{REPO}/{REALM}/index.html"

LEGACY_TEAMS = ("ms_be", "ms_fe")          # archived legacy teams (idempotent drop)

# live team key -> (engine fl1 staging key, engine fl2 staging-card key)
MAP = collections.OrderedDict([
    ("mi_fw",  ("mifw", "mifw")),
    ("mi_os",  ("mios", "minfra")),
    ("mi_be",  ("mibe", "minfra")),
    ("ms_rk",  ("bs",   "bs")),
    ("fithub", ("fh",   "fh")),
    ("msw_be", ("be",   "mswbe")),
    ("msw_cr", ("cr",   "mswcore")),
    ("msw_acq",("aq",   "mswacq")),
])

# report VIEW (Variant B): epic card id -> (display name, live team whose fl2 is canonical)
EPIC_VIEW = collections.OrderedDict([
    ("mifw",    ("MI Firmware",            "mi_fw")),
    ("minfra",  ("Machine Infrastructure", "mi_os")),
    ("fh",      ("Fitness Hub",            "fithub")),
    ("bs",      ("Backstage",              "ms_rk")),
    ("mswbe",   ("MSW Backend",            "msw_be")),
    ("mswcore", ("MSW Core",               "msw_cr")),
    ("mswacq",  ("MSW Acq",                "msw_acq")),
])
FL1_GROUPS = [
    {"title": "", "ids": ["mi_fw", "mi_os", "mi_be", "ms_rk", "fithub"]},
    {"title": "", "ids": ["msw_be", "msw_cr", "msw_acq"]},
]
CORRECTIONS = (
    "Data source moved to the automated Jira engine from Jul 2026; earlier months are the previous "
    "manual monthly reports, so the Jun-to-Jul step may show a one-time discontinuity that reflects the "
    "source/method change rather than a real performance shift &mdash; trend arrows across this boundary "
    "should be read with care. Epic WIP-aging is computed with one consistent percentile method (P70/P85) "
    "for all teams; the legend keeps the historical MSW/MI weekly wording for continuity. MI Firmware "
    "throughput was under-reported in earlier reports (board status-mapping bug, now fixed). "
    "For Aug 2026: Backstage's task WIP-Green count rose from 4 to 9 &mdash; a genuine burst of "
    "newly-started work (all items 11-29 days old, well under the team's own cycle-time threshold), "
    "not a tracking change. Fitness Hub's epic WIP-Green count fell from 5 to 2 as six epics were "
    "delivered this month (above the recent average), leaving a smaller in-flight pool weighted "
    "toward two long-running items (600+ days old); whether those two should be excluded as "
    "evergreen epics is under review with the LDA team."
)

# Slack delivery (exact approved template + tags)
CHANNEL   = "C9CBU5S3C"                       # #sw-team-lda
LDA_IDS   = ["U0A5C8TS79U", "U0AUKK7MKNE", "U0AEVJZ1KTL"]  # Anna Herpel, Todor Todorov, Fabiano Freire
ESCALATE  = "UG3UBQWDP"                       # Alexa Bobina


# ── helpers ────────────────────────────────────────────────────────────────────
def _today(override):
    if override:
        y, m, d = map(int, override.split("-"))
        return date(y, m, d)
    return (datetime.now(_TZ).date() if _TZ else datetime.utcnow().date())

def _labels(today, anchor_override=None, cadence_override=None):
    # anchor = the true (possibly rolled) cadence anchor used for the JQL window and
    # the data-through date; cadence month names the report (Alexa 2026-07-30).
    if anchor_override:
        y, m, d = map(int, anchor_override.split("-"))
        anchor = date(y, m, d)
    else:
        anchor = cj.report_anchor(today.year, today.month, REALM)
    if cadence_override:
        cy, cm = map(int, cadence_override.split("-"))
    else:
        cy, cm = anchor.year, anchor.month
    month = f"{cj.MONTHS_ABBR[cm - 1]} {cy % 100:02d}"                  # e.g. "Jan 27"
    month_full = date(cy, cm, 1).strftime("%B %Y")                     # e.g. "January 2027"
    return anchor, month, month_full, anchor.strftime("%-d %b %Y")

def _stage_paths(month):
    m = month.replace(" ", "_")
    return (os.path.join(DIR, f"machine_stage_teams_{m}.json"),
            os.path.join(DIR, f"machine_stage_cards_{m}.json"))

def _arg(argv, name):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(name + "=")), None)

def _push_data_json(gh_conn, data, sha, msg):
    content = json.dumps(data)
    return call_tool("github_create_or_update_file", {
        "connectionId": gh_conn, "owner": OWNER, "repo": REPO, "path": "data-machine.json",
        "message": msg, "content": content, "sha": sha})


# ── PUBLISH: controlled append + push + dashboards ─────────────────────────────
def publish(jira_conn, gh_conn, today, anchor_override=None, cadence_override=None):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    teams_path, cards_path = _stage_paths(month)
    print(f"=== Machine publish ({month}, anchor {anchor}) ===")

    tv_teams = json.load(open(teams_path)) if os.path.exists(teams_path) else {}
    tv_cards = json.load(open(cards_path)) if os.path.exists(cards_path) else {}
    miss_t = [ef1 for (ef1, _ef2) in MAP.values() if ef1 not in tv_teams]
    miss_c = sorted({ef2 for (_ef1, ef2) in MAP.values() if ef2 not in tv_cards})
    if miss_t or miss_c:
        print(f"  STAGING INCOMPLETE — missing team keys {miss_t}, card keys {miss_c}.")
        print(f"  Run the compute parts first: machine_monthly.py ... --part=fl1a|fl1b|fl2a|fl2b")
        sys.exit(2)

    gen.GH_CONN = gh_conn
    data, sha = gen.load_data()
    realm = data["realms"][REALM]

    # idempotent guard
    if month in realm.get("months", []):
        print(f"--- {month} already present in live data-machine.json — idempotent skip (no push). ---")
        print("  (Regenerating dashboards anyway to be safe.)")
        gen.run(gh_conn, [REALM])
        return {"status": "skipped", "month": month}

    # archive legacy teams (idempotent)
    for k in LEGACY_TEAMS:
        realm["teams"].pop(k, None)
    if set(realm["teams"].keys()) != set(MAP.keys()):
        print(f"  ! live team set {sorted(realm['teams'])} != expected {sorted(MAP)}")
        sys.exit(1)

    # append the new month
    realm["months"] = realm["months"] + [month]
    n = len(realm["months"])
    for lk, (ef1, ef2) in MAP.items():
        t = realm["teams"][lk]
        f1 = tv_teams[ef1]["fl1"]
        for key in t["fl1"]:
            t["fl1"][key] = t["fl1"][key] + [f1.get(key)]
        f2 = tv_cards[ef2]["fl2"]
        for key in t["fl2"]:
            t["fl2"][key] = t["fl2"][key] + [f2.get(key)]

    # validate all series lengths line up with months
    for lk, t in realm["teams"].items():
        for fl in ("fl1", "fl2"):
            for k, arr in t[fl].items():
                assert len(arr) == n, (lk, fl, k, len(arr), n)
    print(f"  APPEND OK  teams={len(realm['teams'])} months={n} ({realm['months'][0]}..{realm['months'][-1]})")

    # safety backup then push
    json.dump(data, open(os.path.join(DIR, f"data_live_backup_{anchor.isoformat()}.json"), "w"))
    res = _push_data_json(gh_conn, data, sha, f"Data (auto): Machine {month}")
    print(f"  pushed data.json (commit {str(res.get('commit', {}).get('sha', ''))[:12]})")

    gen.run(gh_conn, [REALM])
    print(f"  PUBLISHED + dashboards regenerated: Machine {month}")
    return {"status": "updated", "month": month}


# ── REPORT: build the Variant-B PDF (agent-authored notes injected) ────────────
def build_report(gh_conn, today, notes_path, out_path, anchor_override=None, cadence_override=None):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    gen.GH_CONN = gh_conn
    data, _ = gen.load_data()
    realm = data["realms"][REALM]
    if month not in realm.get("months", []):
        print(f"  ! {month} not yet in live data-machine.json — run --publish first.")
        sys.exit(1)

    notes = None
    if notes_path and os.path.exists(notes_path):
        try:
            notes = json.load(open(notes_path))
        except Exception as e:
            print(f"  WARNING: could not read notes {notes_path}: {e}; building without narrative.")

    rv = copy.deepcopy(realm)
    rv["name"] = "Machine Realm"
    rv["fl1_groups"] = FL1_GROUPS
    ec = collections.OrderedDict()
    for cid, (disp, live_team) in EPIC_VIEW.items():
        ec[cid] = {"name": disp, "fl2": copy.deepcopy(realm["teams"][live_team]["fl2"])}
    rv["epic_cards"] = ec
    rv["corrections"] = CORRECTIONS

    if not out_path:
        out_path = os.path.join(DIR, f"Machine_Realm_flow_metrics_{anchor.strftime('%Y_%m')}.pdf")
    bp.build_realm_pdf(rv, REALM, anchor.isoformat(), month_full, out_path, notes=notes)
    print(f"  PDF written: {out_path}")
    return out_path


# ── DELIVER: post to the LDA channel (exact approved template) ─────────────────
def deliver(slack_conn, today, pdf_path, anchor_override=None, cadence_override=None):
    anchor, month, month_full, report_date = _labels(today, anchor_override, cadence_override)
    if not (pdf_path and os.path.exists(pdf_path)):
        print(f"  ! PDF not found: {pdf_path}")
        sys.exit(1)
    tags = " ".join(f"<@{u}>" for u in LDA_IDS)
    msg = (f"\U0001F4CA *Machine Realm \u2014 Flow Metrics \u00b7 {month_full}*\n"
           f"The {month_full} flow metrics report for the *Machine Realm* is ready.\n"
           f"\U0001F4C4 PDF attached \u00b7 \U0001F4C8 Live dashboard: <{DASH}|Machine Realm dashboard>\n\n"
           f"{tags} \u2014 please review. If everything looks good, please share onward with your "
           f"Head of Realm both \u2013 the PDF and the link to the dashboard. If anything looks off, "
           f"please reach out to <https://app.dataleap.ai/agents/a_x9t1v9389b6m0cx0mf1v|Agent>.\n\n"
           f"_Automatically generated \u00b7 rolling 120-day window (through {report_date})_")
    call_tool("slack_post_message", {
        "connectionId": slack_conn, "channelId": CHANNEL, "message": msg,
        "attachments": [{"sourcePath": pdf_path, "fileName": os.path.basename(pdf_path),
                          "mimeType": "application/pdf"}]})
    print(f"  DELIVERED Machine {month_full} -> Slack {CHANNEL}")


def main():
    argv = sys.argv[1:]
    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) < 2:
        print("usage: machine_publish.py <jira_conn> <gh_conn> [YYYY-MM-DD] "
              "[--publish | --report --notes=.. --out=.. | --deliver --slack=.. --pdf=..]")
        sys.exit(2)
    jira_conn, gh_conn = pos[0], pos[1]
    today = _today(pos[2] if len(pos) > 2 and pos[2] else None)
    ao = _arg(argv, "--anchor")
    co = _arg(argv, "--cadence")

    if "--publish" in argv:
        publish(jira_conn, gh_conn, today, ao, co)
    elif "--report" in argv:
        build_report(gh_conn, today, _arg(argv, "--notes"), _arg(argv, "--out"), ao, co)
    elif "--deliver" in argv:
        deliver(_arg(argv, "--slack"), today, _arg(argv, "--pdf"), ao, co)
    else:
        print("Nothing to do: pass --publish, --report, or --deliver.")
        sys.exit(2)


if __name__ == "__main__":
    main()
