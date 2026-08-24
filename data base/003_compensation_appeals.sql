-- database/003_compensation_appeals.sql
USE blue_horizon_db;

-- Working data for the Compensation Appeal Agent.
-- This is separate from the general workflow_runs table and from
-- compensation, which stores the original compensation case before the appeal.
-- appeal_id is linked to exactly one run_id (UNIQUE), because each appeal
-- represents one Run, even if it goes through multiple revised appeal cycles
-- internally.
CREATE TABLE IF NOT EXISTS compensation_appeals (
    appeal_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,

    flight_number VARCHAR(10) NOT NULL,
    passenger_email VARCHAR(100) NOT NULL,

    -- The original compensation record being appealed, if one exists.
    -- NULL when the appeal is against a rejected compensation request
    -- that was never paid.
    original_compensation_id INT NULL,

    appeal_reason TEXT NOT NULL,
    requested_amount DECIMAL(8,2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',

    appeal_status ENUM(
        'awaiting_documents',
        'documents_invalid',
        'under_review',
        'pending_admin_approval',
        'submitting_payment',
        'payment_failed',
        'paid',
        'rejected',
        'closed'
    ) NOT NULL DEFAULT 'awaiting_documents',

    -- Result of the Tree of Thoughts node (compare_appeal_strategies).
    selected_strategy VARCHAR(100) NULL,
    strategy_reasoning TEXT NULL,

    documents_reference JSON NULL,

    -- Reference returned by the mock payment gateway after submit_payment.
    payment_reference VARCHAR(100) NULL,
    payment_gateway_status VARCHAR(50) NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (appeal_id),
    UNIQUE KEY uq_compensation_appeal_run (run_id),
    INDEX idx_compensation_appeals_flight (flight_number),
    INDEX idx_compensation_appeals_status (appeal_status),
    INDEX idx_compensation_appeals_passenger (passenger_email),

    CONSTRAINT fk_compensation_appeals_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_compensation_appeals_original_compensation
        FOREIGN KEY (original_compensation_id) REFERENCES compensation(compensation_id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- Stores every real "revised appeal" cycle in the State Graph.
-- Every time the payment is rejected or the admin rejects the appeal,
-- a new row is inserted here before the graph returns to
-- compare_appeal_strategies with a new strategy.
-- This provides concrete database evidence that a real loop occurred,
-- rather than being just a simple retry.
CREATE TABLE IF NOT EXISTS compensation_appeal_revisions (
    revision_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    appeal_id BIGINT UNSIGNED NOT NULL,
    revision_number INT UNSIGNED NOT NULL,

    trigger_reason ENUM(
        'payment_rejected',
        'admin_rejected'
    ) NOT NULL,

    requested_amount DECIMAL(8,2) NOT NULL,
    selected_strategy VARCHAR(100) NULL,
    strategy_reasoning TEXT NULL,

    outcome ENUM('paid', 'rejected', 'pending') NOT NULL DEFAULT 'pending',
    payment_reference VARCHAR(100) NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (revision_id),
    UNIQUE KEY uq_appeal_revision (appeal_id, revision_number),
    INDEX idx_appeal_revisions_appeal (appeal_id),

    CONSTRAINT fk_appeal_revisions_appeal
        FOREIGN KEY (appeal_id) REFERENCES compensation_appeals(appeal_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- Initial configuration for the Compensation Appeal Agent.
-- submit_compensation_payment is enabled by default because it is already
-- protected inside the node itself (constrained_action) by checking the
-- requested amount against the allowed limit.
-- The admin permission here acts as an additional control layer,
-- similar to mark_flight_ready.
INSERT INTO agent_tool_permissions (agent_name, tool_name, is_enabled)
VALUES
    ('compensation_appeal', 'get_flight_status', 1),
    ('compensation_appeal', 'search_policy_manual', 1),
    ('compensation_appeal', 'submit_compensation_payment', 1)
ON DUPLICATE KEY UPDATE
    is_enabled = VALUES(is_enabled);