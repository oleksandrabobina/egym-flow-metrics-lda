CREATE TABLE IF NOT EXISTS raw_incidents (
 incident_id INTEGER PRIMARY KEY,
 realm VARCHAR(50),
 status VARCHAR(50),
 severity VARCHAR(50),
 affected_team TEXT,
 affected_products TEXT,
 root_cause_service TEXT,
 impact_started_at DATETIME,
 identified_at DATETIME,
 fixed_at DATETIME,
 resolved_at DATETIME,
 updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS realms_teams (
 team_id INTEGER PRIMARY KEY AUTOINCREMENT,
 team_name VARCHAR(100) UNIQUE,
 realm_name VARCHAR(100),
 slack_channel VARCHAR(100),
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS service_realm_registry (
 service_id INTEGER PRIMARY KEY AUTOINCREMENT,
 service_name VARCHAR(100) UNIQUE,
 source_type VARCHAR(20), -- 'github' or 'jira'
 team_name VARCHAR(100),
 realm_name VARCHAR(100),
 confidence_score REAL DEFAULT 1.0,
 is_approved BOOLEAN DEFAULT TRUE,
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployments (
 deployment_id VARCHAR(100) PRIMARY KEY,
 service_name VARCHAR(100),
 realm_name VARCHAR(50),
 team_name VARCHAR(50),
 source VARCHAR(20), -- 'github' or 'jira'
 status VARCHAR(20),
 deployed_at DATETIME,
 FOREIGN KEY(service_name) REFERENCES service_realm_registry(service_name)
);

CREATE TABLE IF NOT EXISTS calculated_dora_metrics (
 incident_id INTEGER PRIMARY KEY,
 month_year VARCHAR(10),
 mttd_hours REAL,
 mttr_hours REAL,
 duration_hours REAL,
 is_change_failure BOOLEAN,
 FOREIGN KEY(incident_id) REFERENCES raw_incidents(incident_id)
);