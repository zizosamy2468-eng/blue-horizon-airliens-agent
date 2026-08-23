USE blue_horizon_db;

-- كل Workflow أو State Graph يتم تشغيله في النظام.
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id CHAR(36) NOT NULL,
    workflow_type VARCHAR(80) NOT NULL,
    flight_number VARCHAR(10) NULL,
    status ENUM(
        'running',
        'waiting_external',
        'waiting_admin',
        'failed',
        'completed',
        'cancelled'
    ) NOT NULL DEFAULT 'running',
    current_node VARCHAR(100) NOT NULL,
    state_json JSON NOT NULL,
    context_json JSON NULL,
    failure_count INT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (run_id),
    INDEX idx_workflow_runs_status (status),
    INDEX idx_workflow_runs_flight_number (flight_number)
) ENGINE=InnoDB;


-- Snapshot كامل للـState بعد كل انتقال مهم داخل الـGraph.
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    checkpoint_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,
    checkpoint_number INT UNSIGNED NOT NULL,
    node_name VARCHAR(100) NOT NULL,
    transition_name VARCHAR(100) NOT NULL,
    state_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (checkpoint_id),
    UNIQUE KEY uq_workflow_checkpoint (run_id, checkpoint_number),
    INDEX idx_checkpoints_run_id (run_id),

    CONSTRAINT fk_checkpoints_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- طلبات Human-in-the-Loop المتوقعة: موافقة أو رفض أدمن.
CREATE TABLE IF NOT EXISTS admin_tasks (
    task_id CHAR(36) NOT NULL,
    run_id CHAR(36) NOT NULL,
    task_type VARCHAR(80) NOT NULL,
    status ENUM('pending', 'approved', 'rejected', 'cancelled')
        NOT NULL DEFAULT 'pending',
    requested_by VARCHAR(100) NOT NULL,
    request_message TEXT NOT NULL,
    request_payload JSON NULL,
    decision_by VARCHAR(100) NULL,
    decision_comment TEXT NULL,
    decision_payload JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME NULL,

    PRIMARY KEY (task_id),
    INDEX idx_admin_tasks_status (status),
    INDEX idx_admin_tasks_run_id (run_id),

    CONSTRAINT fk_admin_tasks_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- أخطاء غير متوقعة، منفصلة تمامًا عن طلبات الـHITL.
CREATE TABLE IF NOT EXISTS failure_tickets (
    ticket_id CHAR(36) NOT NULL,
    run_id CHAR(36) NOT NULL,
    failed_node VARCHAR(100) NOT NULL,
    status ENUM('open', 'investigating', 'resolved', 'closed')
        NOT NULL DEFAULT 'open',
    error_type VARCHAR(120) NOT NULL,
    error_message TEXT NOT NULL,
    state_json JSON NOT NULL,
    resolution_notes TEXT NULL,
    resolved_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME NULL,

    PRIMARY KEY (ticket_id),
    INDEX idx_failure_tickets_status (status),
    INDEX idx_failure_tickets_run_id (run_id),

    CONSTRAINT fk_failure_tickets_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- الأدمن يحدد أي MCP Tools متاحة لكل Agent من المنصة.
CREATE TABLE IF NOT EXISTS agent_tool_permissions (
    agent_name VARCHAR(80) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    is_enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_by VARCHAR(100) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (agent_name, tool_name),
    INDEX idx_agent_tool_permissions_agent (agent_name)
) ENGINE=InnoDB;


-- الإعداد المبدئي لـMaintenance Release Agent.
INSERT INTO agent_tool_permissions (agent_name, tool_name, is_enabled)
VALUES
    ('maintenance_release', 'get_flight_status', 1),
    ('maintenance_release', 'search_policy_manual', 1),
    ('maintenance_release', 'assign_reserve_crew', 0)
ON DUPLICATE KEY UPDATE
    is_enabled = VALUES(is_enabled);

-- Final write tool used only after maintenance clearance
-- and operations-manager approval.
INSERT INTO agent_tool_permissions (
    agent_name,
    tool_name,
    is_enabled
)
VALUES (
    'maintenance_release',
    'mark_flight_ready',
    1
)
ON DUPLICATE KEY UPDATE
    is_enabled = VALUES(is_enabled);    