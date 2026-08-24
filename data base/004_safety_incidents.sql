-- ============================================================
-- 004_safety_incidents.sql
-- Safety Incident Agent schema + tool permissions
-- Owned by: Adel
-- ============================================================

-- Domain table for safety incidents linked to workflow runs.
CREATE TABLE IF NOT EXISTS safety_incidents (
    incident_id CHAR(36) NOT NULL,
    run_id CHAR(36) NOT NULL,
    flight_number VARCHAR(20) NOT NULL,
    severity ENUM('low', 'medium', 'high', 'critical') NOT NULL DEFAULT 'medium',
    incident_type VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    status ENUM(
        'open',
        'investigating',
        'awaiting_report',
        'awaiting_hitl',
        'submitted',
        'acknowledged',
        'closed',
        'failed'
    ) NOT NULL DEFAULT 'open',
    crew_facts JSON NULL,
    ground_report JSON NULL,
    evidence_summary JSON NULL,
    draft_report TEXT NULL,
    final_report TEXT NULL,
    authority_reference VARCHAR(120) NULL,
    created_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (incident_id),
    INDEX idx_safety_incidents_run_id (run_id),
    INDEX idx_safety_incidents_flight (flight_number),
    INDEX idx_safety_incidents_status (status),

    CONSTRAINT fk_safety_incidents_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- Optional regulatory submissions log (authority acknowledgement trail).
CREATE TABLE IF NOT EXISTS safety_authority_submissions (
    submission_id CHAR(36) NOT NULL,
    incident_id CHAR(36) NOT NULL,
    run_id CHAR(36) NOT NULL,
    authority_name VARCHAR(120) NOT NULL DEFAULT 'National Aviation Authority',
    report_payload TEXT NOT NULL,
    status ENUM('pending', 'acknowledged', 'rejected') NOT NULL DEFAULT 'pending',
    acknowledgement_ref VARCHAR(120) NULL,
    submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at DATETIME NULL,

    PRIMARY KEY (submission_id),
    INDEX idx_safety_submissions_incident (incident_id),
    INDEX idx_safety_submissions_run (run_id),

    CONSTRAINT fk_safety_submissions_incident
        FOREIGN KEY (incident_id) REFERENCES safety_incidents(incident_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- Tool permissions for the Safety Incident Agent.
INSERT INTO agent_tool_permissions (agent_name, tool_name, is_enabled)
VALUES
    ('safety_incident', 'get_flight_status', 1),
    ('safety_incident', 'search_policy_manual', 1),
    ('safety_incident', 'create_failure_ticket', 1),
    ('safety_incident', 'submit_regulatory_report', 1)
ON DUPLICATE KEY UPDATE
    is_enabled = VALUES(is_enabled);
