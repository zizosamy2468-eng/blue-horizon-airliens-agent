USE blue_horizon_db;

-- بيانات العمل الخاصة بـMaintenance Release Coordinator.
-- هذا الجدول منفصل عن workflow_runs لأن workflow_runs عام لكل Agents،
-- بينما هذا الجدول خاص بحالات صيانة الطائرات.
CREATE TABLE IF NOT EXISTS maintenance_cases (
    maintenance_case_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,
    flight_number VARCHAR(10) NOT NULL,

    report_status ENUM(
        'awaiting_report',
        'cleared',
        'not_cleared'
    ) NOT NULL DEFAULT 'awaiting_report',

    report_reference VARCHAR(100) NULL,
    report_summary TEXT NULL,
    report_received_at DATETIME NULL,

    operations_decision ENUM(
        'pending',
        'approved',
        'rejected'
    ) NOT NULL DEFAULT 'pending',

    released_by VARCHAR(100) NULL,
    released_at DATETIME NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (maintenance_case_id),
    UNIQUE KEY uq_maintenance_case_run (run_id),
    INDEX idx_maintenance_cases_flight (flight_number),
    INDEX idx_maintenance_cases_report_status (report_status),

    CONSTRAINT fk_maintenance_cases_workflow_run
        FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;