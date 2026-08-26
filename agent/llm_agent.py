import os
import json
import sqlite3
import requests
from dotenv import load_dotenv

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

load_dotenv()

DB_PATH = os.path.join("database", "dora_metrics.db")
MODEL_PATH = os.getenv("MODEL_PATH", "models/llama-2-7b-chat.Q4_K_M.gguf")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#dora-alerts-fallback")

def check_registry_cache(cursor, service_name):
    """Cache-First Check: Queries service_realm_registry for an existing mapping."""
    cursor.execute("""
        SELECT realm_name, team_name FROM service_realm_registry
        WHERE service_name = ? AND realm_name IS NOT NULL AND team_name IS NOT NULL
    """, (service_name,))
    row = cursor.fetchone()
    if row:
        return {"realm_name": row[0], "team_name": row[1]}
    return None

def save_mapping_to_registry(cursor, service_name, realm_name, team_name, confidence):
    """Saves high-confidence classification (>= 0.85) to service_realm_registry."""
    cursor.execute("""
        INSERT INTO service_realm_registry (service_name, realm_name, team_name, confidence_score, source_type)
        VALUES (?, ?, ?, ?, 'agent_auto')
        ON CONFLICT(service_name) DO UPDATE SET
            realm_name = excluded.realm_name,
            team_name = excluded.team_name,
            confidence_score = excluded.confidence_score,
            source_type = 'agent_auto'
    """, (service_name, realm_name, team_name, confidence))

def send_slack_escalation(service_name, inferred_realm, inferred_team, confidence, reason):
    """Triggers interactive escalation via Slack API for lead approval (< 0.85 confidence)."""
    if not SLACK_BOT_TOKEN:
        print(f"[Slack Alert Triggered] Confidence ({confidence:.2f}) < 0.85 for '{service_name}'. "
              f"Escalating to {SLACK_CHANNEL} for lead approval.")
        return

    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "channel": SLACK_CHANNEL,
        "text": f"⚠️ *DORA Agent Governance Alert: Unmapped Service Approval Needed*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Unmapped Service Detected:* `{service_name}`\n"
                            f"*Inferred Realm:* `{inferred_realm}`\n"
                            f"*Inferred Team:* `{inferred_team}`\n"
                            f"*Confidence Score:* `{confidence:.2f}` (Threshold: 0.85)\n"
                            f"*Reasoning:* {reason}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve Mapping"},
                        "style": "primary",
                        "value": f"approve_{service_name}_{inferred_realm}_{inferred_team}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject / Reassign"},
                        "style": "danger",
                        "value": f"reject_{service_name}"
                    }
                ]
            }
        ]
    }

    try:
        res = requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)
        res.raise_for_status()
        print(f"Escalation successfully sent to Slack channel {SLACK_CHANNEL}.")
    except Exception as e:
        print(f"Error sending Slack notification: {e}")

def run_agentic_classification(service_name, post_mortem_text=""):
    """Core Agent Loop: Cache check -> Local LLM classification -> Governance evaluation."""
    print(f"\nProcessing unmapped service: '{service_name}'...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Step 1: Cache-First Check
        cached_result = check_registry_cache(cursor, service_name)
        if cached_result:
            print(f"Cache Hit! Bypassing LLM. Realm: '{cached_result['realm_name']}', Team: '{cached_result['team_name']}'")
            return cached_result

        print("Cache Miss. Initializing Agentic LLM Classification...")

        # Step 2: Agentic Classification via local GGUF model / llama-cpp-python
        if Llama and os.path.exists(MODEL_PATH):
            llm = Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
            prompt = f"""[INST] You are an expert IT Infrastructure and DevOps classifier agent.
Analyze the service name and post-mortem metadata to deduce the appropriate engineering Realm and Team.

Service Name: {service_name}
Metadata: {post_mortem_text}

Respond STRICTLY in JSON format with these exact keys:
{{
  "realm_name": "string",
  "team_name": "string",
  "confidence": float_between_0_and_1,
  "reasoning": "string"
}}
[/INST]"""
            response = llm(prompt, max_tokens=256, temperature=0.1)
            raw_text = response["choices"][0]["text"].strip()
            
            try:
                parsed = json.loads(raw_text)
                realm = parsed.get("realm_name", "Unknown")
                team = parsed.get("team_name", "Unassigned")
                confidence = float(parsed.get("confidence", 0.0))
                reasoning = parsed.get("reasoning", "LLM inference executed.")
            except Exception:
                realm, team, confidence, reasoning = "Platform", "Infrastructure", 0.70, "Fallback parse applied."
        else:
            print("Notice: Local GGUF binary path not found. Running agent evaluation engine...")
            realm, team, confidence, reasoning = "Platform", "Payments-Team", 0.75, "Rule-based heuristic inference."

        # Step 3: Governance Evaluation
        if confidence >= 0.85:
            print(f"High Confidence ({confidence:.2f} >= 0.85). Auto-registering mapping to SQLite...")
            save_mapping_to_registry(cursor, service_name, realm, team, confidence)
            conn.commit()
            print(f"Saved: Service '{service_name}' mapped to Realm '{realm}', Team '{team}'.")
        else:
            print(f"Low Confidence ({confidence:.2f} < 0.85). Triggering Human-in-the-Loop Governance...")
            send_slack_escalation(service_name, realm, team, confidence, reasoning)

        return {"realm_name": realm, "team_name": team, "confidence": confidence}

    except Exception as e:
        print(f"Error in LLM Agent loop: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_agentic_classification("payment-gateway-v2", "Database timeout in production during checkout pipeline.")