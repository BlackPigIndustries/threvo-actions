SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.referential_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_effect_proposal_fk'
    ),
    'ALTER TABLE threvo_actions_effect_claims DROP FOREIGN KEY threvo_actions_effect_proposal_fk',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_json_created'
    ),
    'ALTER TABLE threvo_actions_proposals DROP CHECK threvo_actions_json_created',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_json_expires'
    ),
    'ALTER TABLE threvo_actions_proposals DROP CHECK threvo_actions_json_expires',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_effect_claims'
          AND index_name = 'threvo_actions_effect_proposal_fk'
    ),
    'ALTER TABLE threvo_actions_effect_claims DROP INDEX threvo_actions_effect_proposal_fk',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_effect_identity_digest'
    ),
    'ALTER TABLE threvo_actions_effect_claims DROP CHECK threvo_actions_effect_identity_digest',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_json_required_shape'
    ),
    'ALTER TABLE threvo_actions_proposals DROP CHECK threvo_actions_json_required_shape',
    'DO 0'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_proposals'
          AND column_name = 'effect_identity'
    ),
    'DO 0',
    'ALTER TABLE threvo_actions_proposals ADD COLUMN effect_identity BINARY(32) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(OCTET_LENGTH(action_namespace), '':'', action_namespace, OCTET_LENGTH(action_name), '':'', action_name, OCTET_LENGTH(CAST(action_version AS CHAR)), '':'', action_version, OCTET_LENGTH(semantic_effect_reference), '':'', semantic_effect_reference), 256))) STORED'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
UPDATE threvo_actions_effect_claims
SET effect_identity = UNHEX(SHA2(CONCAT(
    OCTET_LENGTH(action_namespace), ':', action_namespace,
    OCTET_LENGTH(action_name), ':', action_name,
    OCTET_LENGTH(CAST(action_version AS CHAR)), ':', action_version,
    OCTET_LENGTH(semantic_effect_reference), ':', semantic_effect_reference
), 256));
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_proposals'
          AND index_name = 'threvo_actions_proposal_effect_identity_uq'
    ),
    'DO 0',
    'ALTER TABLE threvo_actions_proposals ADD UNIQUE INDEX threvo_actions_proposal_effect_identity_uq (tenant_reference, proposal_reference, effect_identity)'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_effect_claims'
          AND index_name = 'threvo_actions_effect_proposal_fk'
    ),
    'DO 0',
    'ALTER TABLE threvo_actions_effect_claims ADD INDEX threvo_actions_effect_proposal_fk (tenant_reference, proposal_reference, effect_identity)'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
SET @threvo_actions_ddl = IF(
    EXISTS (
        SELECT 1 FROM information_schema.referential_constraints
        WHERE constraint_schema = DATABASE()
          AND constraint_name = 'threvo_actions_effect_proposal_fk'
    ),
    'DO 0',
    'ALTER TABLE threvo_actions_effect_claims ADD CONSTRAINT threvo_actions_effect_proposal_fk FOREIGN KEY (tenant_reference, proposal_reference, effect_identity) REFERENCES threvo_actions_proposals (tenant_reference, proposal_reference, effect_identity)'
);
-- threvo-actions:next
PREPARE threvo_actions_migration_ddl FROM @threvo_actions_ddl;
-- threvo-actions:next
EXECUTE threvo_actions_migration_ddl;
-- threvo-actions:next
DEALLOCATE PREPARE threvo_actions_migration_ddl;
-- threvo-actions:next
ALTER TABLE threvo_actions_effect_claims
ADD CONSTRAINT threvo_actions_effect_identity_digest CHECK (
    effect_identity = UNHEX(SHA2(CONCAT(
        OCTET_LENGTH(action_namespace), ':', action_namespace,
        OCTET_LENGTH(action_name), ':', action_name,
        OCTET_LENGTH(CAST(action_version AS CHAR)), ':', action_version,
        OCTET_LENGTH(semantic_effect_reference), ':', semantic_effect_reference
    ), 256))
);
-- threvo-actions:next
DROP TRIGGER IF EXISTS threvo_actions_enforce_proposal_update;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_migrate_v1_datetime;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_migrate_v1_datetime(
    INOUT p_proposal_data JSON,
    IN p_json_path VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin
)
SQL SECURITY DEFINER
NO SQL
BEGIN
    DECLARE text_value TEXT;
    DECLARE parsed_value DATETIME(6);
    DECLARE offset_minutes INT;

    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, p_json_path)) = 'STRING' THEN
        SET text_value = JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, p_json_path));
        IF RIGHT(text_value, 1) <> 'Z' THEN
            IF NOT REGEXP_LIKE(
                text_value COLLATE utf8mb4_bin,
                '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?[+-]([01][0-9]|2[0-3]):[0-5][0-9]$',
                'c'
            ) THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT =
                    'version-one proposal contains an invalid aware datetime';
            END IF;
            SET parsed_value = STR_TO_DATE(
                LEFT(text_value, CHAR_LENGTH(text_value) - 6),
                IF(
                    LOCATE('.', text_value) > 0,
                    '%Y-%m-%dT%H:%i:%s.%f',
                    '%Y-%m-%dT%H:%i:%s'
                )
            );
            SET offset_minutes =
                CAST(SUBSTRING(text_value, CHAR_LENGTH(text_value) - 4, 2) AS UNSIGNED) * 60 +
                CAST(RIGHT(text_value, 2) AS UNSIGNED);
            IF SUBSTRING(text_value, CHAR_LENGTH(text_value) - 5, 1) = '+' THEN
                SET offset_minutes = -offset_minutes;
            END IF;
            SET parsed_value = TIMESTAMPADD(MINUTE, offset_minutes, parsed_value);
            IF parsed_value IS NULL THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT =
                    'version-one proposal datetime cannot be represented by MySQL';
            END IF;
            SET p_proposal_data = JSON_SET(
                p_proposal_data,
                p_json_path,
                CONCAT(
                    DATE_FORMAT(parsed_value, '%Y-%m-%dT%H:%i:%s'),
                    IF(
                        MICROSECOND(parsed_value) = 0,
                        '',
                        CONCAT(
                            '.',
                            LPAD(CAST(MICROSECOND(parsed_value) AS CHAR), 6, '0')
                        )
                    ),
                    'Z'
                )
            );
        END IF;
    END IF;
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_canonicalize_v1_datetimes;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_canonicalize_v1_datetimes()
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    DECLARE finished BOOLEAN DEFAULT FALSE;
    DECLARE current_tenant_reference VARCHAR(255)
        CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
    DECLARE current_proposal_reference VARCHAR(255)
        CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
    DECLARE current_proposal_data JSON;
    DECLARE sequence_index INT;
    DECLARE proposal_cursor CURSOR FOR
        SELECT tenant_reference, proposal_reference, proposal_data
        FROM threvo_actions_proposals;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET finished = TRUE;

    OPEN proposal_cursor;
    proposal_loop: LOOP
        FETCH proposal_cursor INTO
            current_tenant_reference, current_proposal_reference, current_proposal_data;
        IF finished THEN
            LEAVE proposal_loop;
        END IF;
        CALL threvo_actions_migrate_v1_datetime(current_proposal_data, '$.created_at');
        CALL threvo_actions_migrate_v1_datetime(current_proposal_data, '$.expires_at');
        CALL threvo_actions_migrate_v1_datetime(
            current_proposal_data, '$.next_verification_at'
        );
        CALL threvo_actions_migrate_v1_datetime(
            current_proposal_data, '$.erasure_pending_at'
        );
        CALL threvo_actions_migrate_v1_datetime(current_proposal_data, '$.erased_at');

        SET sequence_index = 0;
        WHILE sequence_index < COALESCE(JSON_LENGTH(JSON_EXTRACT(
            current_proposal_data, '$.authority_evidence'
        )), 0) DO
            CALL threvo_actions_migrate_v1_datetime(
                current_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].issued_at')
            );
            CALL threvo_actions_migrate_v1_datetime(
                current_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].expires_at')
            );
            SET sequence_index = sequence_index + 1;
        END WHILE;

        SET sequence_index = 0;
        WHILE sequence_index < COALESCE(JSON_LENGTH(JSON_EXTRACT(
            current_proposal_data, '$.receipts'
        )), 0) DO
            CALL threvo_actions_migrate_v1_datetime(
                current_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].observed_at')
            );
            SET sequence_index = sequence_index + 1;
        END WHILE;

        UPDATE threvo_actions_proposals
        SET proposal_data = current_proposal_data
        WHERE tenant_reference = current_tenant_reference
          AND proposal_reference = current_proposal_reference;
    END LOOP;
    CLOSE proposal_cursor;
END;
-- threvo-actions:next
CALL threvo_actions_canonicalize_v1_datetimes();
-- threvo-actions:next
DROP PROCEDURE threvo_actions_canonicalize_v1_datetimes;
-- threvo-actions:next
DROP PROCEDURE threvo_actions_migrate_v1_datetime;
-- threvo-actions:next
ALTER TABLE threvo_actions_proposals
ADD CONSTRAINT threvo_actions_json_required_shape CHECK (
    JSON_CONTAINS_PATH(
        proposal_data,
        'all',
        '$.tenant_reference',
        '$.proposal_reference',
        '$.action_type',
        '$.action_type.namespace',
        '$.action_type.name',
        '$.action_type.version',
        '$.semantic_effect_reference',
        '$.effect_kind',
        '$.lifecycle_status',
        '$.revision',
        '$.protected_private_snapshot',
        '$.commitment',
        '$.display_preview',
        '$.requesting_principal',
        '$.proposing_agent',
        '$.created_at',
        '$.expires_at',
        '$.authority_evidence',
        '$.receipts',
        '$.verification_attempts',
        '$.max_verification_attempts',
        '$.next_verification_at',
        '$.safe_result',
        '$.execution_precondition',
        '$.superseded_by',
        '$.erasure_pending_at',
        '$.erased_at'
    ) = 1 AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.tenant_reference')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.proposal_reference')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.action_type')) = 'OBJECT' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.action_type.namespace')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.action_type.name')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(
        proposal_data, '$.action_type.version'
    )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.semantic_effect_reference')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.effect_kind')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.lifecycle_status')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(
        proposal_data, '$.revision'
    )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.protected_private_snapshot')) IN ('NULL', 'OBJECT') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.commitment')) IN ('NULL', 'OBJECT') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.display_preview')) = 'OBJECT' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.requesting_principal')) IN ('NULL', 'OBJECT') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.proposing_agent')) IN ('NULL', 'OBJECT') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.created_at')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.expires_at')) = 'STRING' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.authority_evidence')) = 'ARRAY' AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.receipts')) = 'ARRAY' AND
    JSON_TYPE(JSON_EXTRACT(
        proposal_data, '$.verification_attempts'
    )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
    JSON_TYPE(JSON_EXTRACT(
        proposal_data, '$.max_verification_attempts'
    )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.next_verification_at')) IN ('NULL', 'STRING') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.safe_result')) IN ('NULL', 'OBJECT') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.execution_precondition')) IN ('NULL', 'STRING') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.superseded_by')) IN ('NULL', 'STRING') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erasure_pending_at')) IN ('NULL', 'STRING') AND
    JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erased_at')) IN ('NULL', 'STRING')
);
-- threvo-actions:next
ALTER TABLE threvo_actions_proposals
ADD CONSTRAINT threvo_actions_json_created CHECK (
    REGEXP_LIKE(
        (JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.created_at')) COLLATE utf8mb4_bin),
        '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$',
        'c'
    ) AND
    (STR_TO_DATE(
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.created_at')),
        IF(
            (LOCATE('.', JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.created_at'))) > 0),
            '%Y-%m-%dT%H:%i:%s.%fZ',
            '%Y-%m-%dT%H:%i:%sZ'
        )
    ) = created_at)
);
-- threvo-actions:next
ALTER TABLE threvo_actions_proposals
ADD CONSTRAINT threvo_actions_json_expires CHECK (
    REGEXP_LIKE(
        (JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.expires_at')) COLLATE utf8mb4_bin),
        '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$',
        'c'
    ) AND
    (STR_TO_DATE(
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.expires_at')),
        IF(
            (LOCATE('.', JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.expires_at'))) > 0),
            '%Y-%m-%dT%H:%i:%s.%fZ',
            '%Y-%m-%dT%H:%i:%sZ'
        )
    ) = expires_at)
);
-- threvo-actions:next
CREATE TRIGGER threvo_actions_enforce_proposal_update
BEFORE UPDATE ON threvo_actions_proposals
FOR EACH ROW
BEGIN
    IF NOT (
        NEW.tenant_reference <=> OLD.tenant_reference AND
        NEW.proposal_reference <=> OLD.proposal_reference AND
        NEW.action_namespace <=> OLD.action_namespace AND
        NEW.action_name <=> OLD.action_name AND
        NEW.action_version <=> OLD.action_version AND
        NEW.semantic_effect_reference <=> OLD.semantic_effect_reference AND
        NEW.effect_kind <=> OLD.effect_kind AND
        NEW.created_at <=> OLD.created_at
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'proposal identity cannot change';
    END IF;
    IF NEW.revision <> OLD.revision + 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'proposal revision must advance by one';
    END IF;
    IF NEW.lifecycle_status <> OLD.lifecycle_status AND NOT (
        (OLD.lifecycle_status = 'awaiting_authority' AND NEW.lifecycle_status = 'denied') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'authorized' AND NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'authorized' AND NEW.lifecycle_status = 'blocked') OR
        (OLD.lifecycle_status = 'authorized' AND NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'authorized' AND NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'stale' AND NEW.lifecycle_status = 'superseded') OR
        (OLD.lifecycle_status = 'executing' AND NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'executing' AND NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'executing' AND NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'executing' AND NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'failed_unknown' AND NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'verification_unresolved') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'partially_succeeded') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status = 'verified')
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'invalid proposal lifecycle transition';
    END IF;
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_validate_proposal_data;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_validate_proposal_data(IN p_proposal_data JSON)
SQL SECURITY DEFINER
NO SQL
BEGIN
    DECLARE valid BOOLEAN DEFAULT TRUE;
    DECLARE sequence_index INT DEFAULT 0;
    DECLARE nested_index INT DEFAULT 0;
    DECLARE current_value JSON;
    DECLARE nested_value JSON;
    DECLARE safe_values JSON;
    DECLARE safe_index INT DEFAULT 0;
    DECLARE text_value TEXT;
    DECLARE first_time DATETIME(6);
    DECLARE second_time DATETIME(6);
    DECLARE CONTINUE HANDLER FOR 1411 SET valid = FALSE;

    IF NOT COALESCE((
        JSON_TYPE(p_proposal_data) = 'OBJECT' AND JSON_LENGTH(p_proposal_data) = 24 AND
        JSON_CONTAINS_PATH(
            p_proposal_data, 'all', '$.tenant_reference', '$.proposal_reference',
            '$.action_type', '$.semantic_effect_reference', '$.effect_kind',
            '$.lifecycle_status', '$.revision', '$.protected_private_snapshot',
            '$.commitment', '$.display_preview', '$.requesting_principal',
            '$.proposing_agent', '$.created_at', '$.expires_at', '$.authority_evidence',
            '$.receipts', '$.verification_attempts', '$.max_verification_attempts',
            '$.next_verification_at', '$.safe_result', '$.execution_precondition',
            '$.superseded_by', '$.erasure_pending_at', '$.erased_at'
        ) = 1 AND
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.action_type')) = 'OBJECT' AND
        JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.action_type')) = 3 AND
        REGEXP_LIKE(
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data, '$.action_type.namespace'
            )) COLLATE utf8mb4_bin,
            '^[a-z][a-z0-9_]*([.][a-z][a-z0-9_]*)+$', 'c'
        ) AND
        OCTET_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data, '$.action_type.namespace'
        ))) <= 65535 AND
        REGEXP_LIKE(
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data, '$.action_type.name'
            )) COLLATE utf8mb4_bin,
            '^[a-z][a-z0-9_]*$', 'c'
        ) AND
        OCTET_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data, '$.action_type.name'
        ))) <= 65535 AND
        JSON_TYPE(JSON_EXTRACT(
            p_proposal_data, '$.action_type.version'
        )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
        CAST(JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data, '$.action_type.version'
        )) AS UNSIGNED) BETWEEN 1 AND 4294967295 AND
        JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.effect_kind')) IN ('single', 'itemized') AND
        JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.lifecycle_status')) IN (
            'awaiting_authority', 'denied', 'expired', 'authorized', 'blocked', 'stale',
            'superseded', 'executing', 'failed_known', 'failed_unknown',
            'verification_pending', 'verification_unresolved', 'partially_succeeded', 'verified'
        ) AND
        JSON_TYPE(JSON_EXTRACT(
            p_proposal_data, '$.revision'
        )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
        JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.revision')) NOT LIKE '-%' AND
        CAST(JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data, '$.revision'
        )) AS DECIMAL(20, 0)) <= 18446744073709551615 AND
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.display_preview')) = 'OBJECT' AND
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.authority_evidence')) = 'ARRAY' AND
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.receipts')) = 'ARRAY' AND
        JSON_TYPE(JSON_EXTRACT(
            p_proposal_data, '$.verification_attempts'
        )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
        JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.verification_attempts')) NOT LIKE '-%' AND
        JSON_TYPE(JSON_EXTRACT(
            p_proposal_data, '$.max_verification_attempts'
        )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
        JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.max_verification_attempts')) NOT LIKE '-%' AND
        CAST(JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data, '$.max_verification_attempts'
        )) AS UNSIGNED) >= 1 AND
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.safe_result')) IN ('NULL', 'OBJECT')
    ), FALSE) THEN SET valid = FALSE; END IF;

    SET safe_values = JSON_ARRAY(
        JSON_EXTRACT(p_proposal_data, '$.tenant_reference'),
        JSON_EXTRACT(p_proposal_data, '$.proposal_reference'),
        JSON_EXTRACT(p_proposal_data, '$.semantic_effect_reference')
    );
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.execution_precondition')) <> 'NULL' THEN
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(p_proposal_data, '$.execution_precondition')
        );
    END IF;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.superseded_by')) <> 'NULL' THEN
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(p_proposal_data, '$.superseded_by')
        );
    END IF;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.protected_private_snapshot')) = 'OBJECT' THEN
        SET current_value = JSON_EXTRACT(p_proposal_data, '$.protected_private_snapshot');
        IF JSON_LENGTH(current_value) <> 4 OR
           JSON_TYPE(JSON_EXTRACT(current_value, '$.ciphertext')) <> 'STRING' OR
           CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(
               current_value, '$.ciphertext'
           ))) NOT BETWEEN 1 AND 1048576 THEN SET valid = FALSE; END IF;
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(current_value, '$.codec'),
            '$', JSON_EXTRACT(current_value, '$.key_handle'),
            '$', JSON_EXTRACT(current_value, '$.key_version')
        );
    ELSEIF JSON_TYPE(JSON_EXTRACT(
        p_proposal_data, '$.protected_private_snapshot'
    )) <> 'NULL' THEN SET valid = FALSE; END IF;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.commitment')) = 'OBJECT' THEN
        SET current_value = JSON_EXTRACT(p_proposal_data, '$.commitment');
        IF JSON_LENGTH(current_value) <> 4 OR
           JSON_TYPE(JSON_EXTRACT(current_value, '$.algorithm')) <> 'STRING' THEN
            SET valid = FALSE;
        END IF;
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(current_value, '$.key_handle'),
            '$', JSON_EXTRACT(current_value, '$.key_version'),
            '$', JSON_EXTRACT(current_value, '$.digest')
        );
    ELSEIF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.commitment')) <> 'NULL' THEN
        SET valid = FALSE;
    END IF;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.requesting_principal')) = 'OBJECT' THEN
        SET current_value = JSON_EXTRACT(p_proposal_data, '$.requesting_principal');
        IF JSON_LENGTH(current_value) <> 2 OR
           JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.kind')) <> 'requesting_principal' THEN
            SET valid = FALSE;
        END IF;
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(current_value, '$.reference')
        );
    ELSEIF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.requesting_principal')) <> 'NULL' THEN
        SET valid = FALSE;
    END IF;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.proposing_agent')) = 'OBJECT' THEN
        SET current_value = JSON_EXTRACT(p_proposal_data, '$.proposing_agent');
        IF JSON_LENGTH(current_value) <> 2 OR
           JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.kind')) <> 'proposing_agent' THEN
            SET valid = FALSE;
        END IF;
        SET safe_values = JSON_ARRAY_APPEND(
            safe_values, '$', JSON_EXTRACT(current_value, '$.reference')
        );
    ELSEIF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.proposing_agent')) <> 'NULL' THEN
        SET valid = FALSE;
    END IF;
    SET safe_index = 0;
    WHILE valid AND safe_index < JSON_LENGTH(safe_values) DO
        SET nested_value = JSON_EXTRACT(safe_values, CONCAT('$[', safe_index, ']'));
        SET text_value = JSON_UNQUOTE(nested_value);
        IF NOT COALESCE((
            JSON_TYPE(nested_value) = 'STRING' AND CHAR_LENGTH(text_value) BETWEEN 1 AND 255 AND
            NOT REGEXP_LIKE(
                text_value COLLATE utf8mb4_bin,
                CONCAT('[', CHAR(92 USING utf8mb4), 'p{C}]')
            ) AND
            NOT REGEXP_LIKE(
                text_value COLLATE utf8mb4_bin,
                CONCAT('^[', CHAR(92 USING utf8mb4), 'p{Z}',
                       CHAR(92 USING utf8mb4), 's]|[',
                       CHAR(92 USING utf8mb4), 'p{Z}',
                       CHAR(92 USING utf8mb4), 's]$')
            )
        ), FALSE) THEN SET valid = FALSE; END IF;
        SET safe_index = safe_index + 1;
    END WHILE;

    SET text_value = JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.created_at'));
    IF NOT COALESCE((
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.created_at')) = 'STRING' AND
        REGEXP_LIKE(text_value COLLATE utf8mb4_bin, '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$', 'c')
    ), FALSE) THEN SET valid = FALSE;
    ELSE SET first_time = STR_TO_DATE(
        text_value, IF(INSTR(text_value, '.') > 0, '%Y-%m-%dT%H:%i:%s.%fZ', '%Y-%m-%dT%H:%i:%sZ')
    ); END IF;
    SET text_value = JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.expires_at'));
    IF NOT COALESCE((
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.expires_at')) = 'STRING' AND
        REGEXP_LIKE(text_value COLLATE utf8mb4_bin, '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$', 'c')
    ), FALSE) THEN SET valid = FALSE;
    ELSE
        SET second_time = STR_TO_DATE(
            text_value, IF(INSTR(text_value, '.') > 0, '%Y-%m-%dT%H:%i:%s.%fZ', '%Y-%m-%dT%H:%i:%sZ')
        );
        IF first_time IS NULL OR second_time IS NULL OR second_time <= first_time THEN
            SET valid = FALSE;
        END IF;
    END IF;

    SET sequence_index = 0;
    WHILE valid AND sequence_index < JSON_LENGTH(JSON_EXTRACT(
        p_proposal_data, '$.authority_evidence'
    )) DO
        SET current_value = JSON_EXTRACT(
            p_proposal_data, CONCAT('$.authority_evidence[', sequence_index, ']')
        );
        IF NOT COALESCE((
            JSON_TYPE(current_value) = 'OBJECT' AND
            JSON_LENGTH(current_value) = 14 AND
            JSON_CONTAINS_PATH(
                current_value, 'all', '$.kind', '$.domain', '$.schema_version',
                '$.tenant_reference', '$.action_type', '$.proposal_instance_reference',
                '$.semantic_effect_reference', '$.authority', '$.audience', '$.decision',
                '$.proposal_commitment', '$.channel_assurance', '$.issued_at', '$.expires_at'
            ) = 1 AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.kind')) = 'bound_decision' AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.domain')) =
                'threvo.actions.authority-evidence' AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.schema_version')) = 'internal/v0' AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.action_type')) = 'OBJECT' AND
            JSON_LENGTH(JSON_EXTRACT(current_value, '$.action_type')) = 3 AND
            REGEXP_LIKE(
                JSON_UNQUOTE(JSON_EXTRACT(
                    current_value, '$.action_type.namespace'
                )) COLLATE utf8mb4_bin,
                '^[a-z][a-z0-9_]*([.][a-z][a-z0-9_]*)+$', 'c'
            ) AND
            REGEXP_LIKE(
                JSON_UNQUOTE(JSON_EXTRACT(
                    current_value, '$.action_type.name'
                )) COLLATE utf8mb4_bin,
                '^[a-z][a-z0-9_]*$', 'c'
            ) AND
            JSON_TYPE(JSON_EXTRACT(
                current_value, '$.action_type.version'
            )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
            CAST(JSON_UNQUOTE(JSON_EXTRACT(
                current_value, '$.action_type.version'
            )) AS UNSIGNED) >= 1 AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.authority')) = 'OBJECT' AND
            JSON_LENGTH(JSON_EXTRACT(current_value, '$.authority')) = 2 AND
            JSON_UNQUOTE(JSON_EXTRACT(
                current_value, '$.authority.kind'
            )) = 'confirming_authority' AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.authority.reference')) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.audience')) = 'ARRAY' AND
            JSON_LENGTH(JSON_EXTRACT(current_value, '$.audience')) > 0 AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.decision')) IN ('approve', 'reject') AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.tenant_reference')) <=>
                JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.tenant_reference')) AND
            JSON_EXTRACT(current_value, '$.action_type') <=>
                JSON_EXTRACT(p_proposal_data, '$.action_type') AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.proposal_instance_reference')) <=>
                JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.proposal_reference')) AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.semantic_effect_reference')) <=>
                JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.semantic_effect_reference')) AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.proposal_commitment')) <=>
                JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.commitment.digest'))
        ), FALSE) THEN SET valid = FALSE; END IF;
        SET safe_values = JSON_ARRAY(
            JSON_EXTRACT(current_value, '$.tenant_reference'),
            JSON_EXTRACT(current_value, '$.proposal_instance_reference'),
            JSON_EXTRACT(current_value, '$.semantic_effect_reference'),
            JSON_EXTRACT(current_value, '$.authority.reference'),
            JSON_EXTRACT(current_value, '$.proposal_commitment'),
            JSON_EXTRACT(current_value, '$.channel_assurance')
        );
        SET nested_index = 0;
        WHILE nested_index < JSON_LENGTH(JSON_EXTRACT(current_value, '$.audience')) DO
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(
                    current_value, CONCAT('$.audience[', nested_index, ']')
                )
            );
            SET nested_index = nested_index + 1;
        END WHILE;
        SET safe_index = 0;
        WHILE valid AND safe_index < JSON_LENGTH(safe_values) DO
            SET nested_value = JSON_EXTRACT(safe_values, CONCAT('$[', safe_index, ']'));
            SET text_value = JSON_UNQUOTE(nested_value);
            IF NOT COALESCE((
                JSON_TYPE(nested_value) = 'STRING' AND
                CHAR_LENGTH(text_value) BETWEEN 1 AND 255 AND
                NOT REGEXP_LIKE(
                    text_value COLLATE utf8mb4_bin,
                    CONCAT('[', CHAR(92 USING utf8mb4), 'p{C}]')
                ) AND
                NOT REGEXP_LIKE(
                    text_value COLLATE utf8mb4_bin,
                    CONCAT('^[', CHAR(92 USING utf8mb4), 'p{Z}',
                           CHAR(92 USING utf8mb4), 's]|[',
                           CHAR(92 USING utf8mb4), 'p{Z}',
                           CHAR(92 USING utf8mb4), 's]$')
                )
            ), FALSE) THEN SET valid = FALSE; END IF;
            SET safe_index = safe_index + 1;
        END WHILE;
        SET text_value = JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.issued_at'));
        IF NOT COALESCE((
            JSON_TYPE(JSON_EXTRACT(current_value, '$.issued_at')) = 'STRING' AND
            REGEXP_LIKE(text_value COLLATE utf8mb4_bin, '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$', 'c')
        ), FALSE) THEN SET valid = FALSE;
        ELSE SET first_time = STR_TO_DATE(text_value, IF(INSTR(text_value, '.') > 0, '%Y-%m-%dT%H:%i:%s.%fZ', '%Y-%m-%dT%H:%i:%sZ')); END IF;
        SET text_value = JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.expires_at'));
        IF NOT COALESCE((
            JSON_TYPE(JSON_EXTRACT(current_value, '$.expires_at')) = 'STRING' AND
            REGEXP_LIKE(text_value COLLATE utf8mb4_bin, '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$', 'c')
        ), FALSE) THEN SET valid = FALSE;
        ELSE
            SET second_time = STR_TO_DATE(text_value, IF(INSTR(text_value, '.') > 0, '%Y-%m-%dT%H:%i:%s.%fZ', '%Y-%m-%dT%H:%i:%sZ'));
            IF first_time IS NULL OR second_time IS NULL OR second_time <= first_time THEN
                SET valid = FALSE;
            END IF;
        END IF;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    SET sequence_index = 0;
    WHILE valid AND sequence_index < JSON_LENGTH(JSON_EXTRACT(
        p_proposal_data, '$.receipts'
    )) DO
        SET current_value = JSON_EXTRACT(
            p_proposal_data, CONCAT('$.receipts[', sequence_index, ']')
        );
        IF NOT COALESCE((
            JSON_TYPE(current_value) = 'OBJECT' AND
            JSON_CONTAINS_PATH(
                current_value, 'all', '$.schema_version', '$.receipt_reference',
                '$.correlation_reference', '$.causation_reference', '$.observed_at',
                '$.runtime_revision', '$.external_reference', '$.corrects_receipt_reference',
                '$.supersedes_receipt_reference', '$.reason_code', '$.receipt_type', '$.status'
            ) = 1 AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.schema_version')) = 'internal/v0' AND
            JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.correlation_reference')) <=>
                JSON_UNQUOTE(JSON_EXTRACT(p_proposal_data, '$.proposal_reference')) AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.runtime_revision')) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.external_reference')) IN ('NULL', 'OBJECT') AND
            JSON_TYPE(JSON_EXTRACT(
                current_value, '$.corrects_receipt_reference'
            )) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(
                current_value, '$.supersedes_receipt_reference'
            )) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(current_value, '$.reason_code')) IN ('NULL', 'STRING') AND
            (
                JSON_TYPE(JSON_EXTRACT(current_value, '$.external_reference')) = 'NULL' OR (
                    JSON_LENGTH(JSON_EXTRACT(current_value, '$.external_reference')) = 2 AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.external_reference.system'
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.external_reference.reference'
                    )) = 'STRING'
                )
            ) AND (
                (
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.receipt_type')) = 'proposal' AND
                    JSON_LENGTH(current_value) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.status'
                    )) IN ('prepared', 'failed', 'missing') AND
                    JSON_TYPE(JSON_EXTRACT(current_value, '$.requesting_principal')) = 'OBJECT' AND
                    JSON_LENGTH(JSON_EXTRACT(current_value, '$.requesting_principal')) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.requesting_principal.kind'
                    )) = 'requesting_principal' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.requesting_principal.reference'
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.proposing_agent'
                    )) IN ('NULL', 'OBJECT') AND (
                        JSON_TYPE(JSON_EXTRACT(current_value, '$.proposing_agent')) = 'NULL' OR (
                            JSON_LENGTH(JSON_EXTRACT(current_value, '$.proposing_agent')) = 2 AND
                            JSON_UNQUOTE(JSON_EXTRACT(
                                current_value, '$.proposing_agent.kind'
                            )) = 'proposing_agent' AND
                            JSON_TYPE(JSON_EXTRACT(
                                current_value, '$.proposing_agent.reference'
                            )) = 'STRING'
                        )
                    )
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.receipt_type')) = 'authority' AND
                    JSON_LENGTH(current_value) = 13 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.status'
                    )) IN ('recorded', 'rejected', 'failed', 'missing') AND
                    JSON_LENGTH(JSON_EXTRACT(current_value, '$.participant')) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.participant.kind'
                    )) = 'confirming_authority' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.participant.reference'
                    )) = 'STRING'
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.receipt_type')) = 'execution' AND
                    JSON_LENGTH(current_value) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.status')) IN (
                        'started', 'accepted', 'partially_succeeded', 'stale_no_effect',
                        'failed_known', 'failed_unknown', 'missing'
                    ) AND
                    JSON_LENGTH(JSON_EXTRACT(current_value, '$.participant')) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.participant.kind'
                    )) = 'governed_executor' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.participant.reference'
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(current_value, '$.item_outcomes')) = 'ARRAY'
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.receipt_type')) = 'verification' AND
                    JSON_LENGTH(current_value) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.status')) IN (
                        'verified_completion', 'verified_terminal_failure', 'provisional_absence',
                        'authoritative_final_absence', 'target_unavailable',
                        'verification_unresolved', 'missing'
                    ) AND
                    JSON_LENGTH(JSON_EXTRACT(current_value, '$.participant')) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        current_value, '$.participant.kind'
                    )) = 'authoritative_target' AND
                    JSON_TYPE(JSON_EXTRACT(
                        current_value, '$.participant.reference'
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(current_value, '$.item_outcomes')) = 'ARRAY'
                )
            )
        ), FALSE) THEN SET valid = FALSE; END IF;
        SET safe_values = JSON_ARRAY(
            JSON_EXTRACT(current_value, '$.receipt_reference'),
            JSON_EXTRACT(current_value, '$.correlation_reference'),
            JSON_EXTRACT(current_value, '$.causation_reference')
        );
        IF JSON_TYPE(JSON_EXTRACT(current_value, '$.runtime_revision')) <> 'NULL' THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.runtime_revision')
            );
        END IF;
        IF JSON_TYPE(JSON_EXTRACT(current_value, '$.corrects_receipt_reference')) <> 'NULL' THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.corrects_receipt_reference')
            );
        END IF;
        IF JSON_TYPE(JSON_EXTRACT(current_value, '$.supersedes_receipt_reference')) <> 'NULL' THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.supersedes_receipt_reference')
            );
        END IF;
        IF JSON_TYPE(JSON_EXTRACT(current_value, '$.reason_code')) <> 'NULL' THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.reason_code')
            );
        END IF;
        IF JSON_TYPE(JSON_EXTRACT(current_value, '$.external_reference')) = 'OBJECT' THEN
            SET nested_value = JSON_EXTRACT(current_value, '$.external_reference');
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(nested_value, '$.system'),
                '$', JSON_EXTRACT(nested_value, '$.reference')
            );
        END IF;
        IF JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.receipt_type')) = 'proposal' THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.requesting_principal.reference')
            );
            IF JSON_TYPE(JSON_EXTRACT(current_value, '$.proposing_agent')) = 'OBJECT' THEN
                SET safe_values = JSON_ARRAY_APPEND(
                    safe_values, '$', JSON_EXTRACT(current_value, '$.proposing_agent.reference')
                );
            END IF;
        ELSEIF JSON_UNQUOTE(JSON_EXTRACT(
            current_value, '$.receipt_type'
        )) IN ('authority', 'execution', 'verification') THEN
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(current_value, '$.participant.reference')
            );
        END IF;
        SET nested_index = 0;
        WHILE nested_index < COALESCE(JSON_LENGTH(JSON_EXTRACT(
            current_value, '$.item_outcomes'
        )), 0) DO
            SET nested_value = JSON_EXTRACT(
                current_value, CONCAT('$.item_outcomes[', nested_index, ']')
            );
            IF NOT COALESCE((
                JSON_TYPE(nested_value) = 'OBJECT' AND JSON_LENGTH(nested_value) = 3 AND
                JSON_CONTAINS_PATH(
                    nested_value, 'all', '$.item_reference', '$.status', '$.reason_code'
                ) = 1 AND
                JSON_TYPE(JSON_EXTRACT(nested_value, '$.item_reference')) = 'STRING' AND
                JSON_UNQUOTE(JSON_EXTRACT(
                    nested_value, '$.status'
                )) IN ('succeeded', 'failed_known', 'failed_unknown') AND
                JSON_TYPE(JSON_EXTRACT(nested_value, '$.reason_code')) IN ('NULL', 'STRING')
            ), FALSE) THEN SET valid = FALSE; END IF;
            SET safe_values = JSON_ARRAY_APPEND(
                safe_values, '$', JSON_EXTRACT(nested_value, '$.item_reference')
            );
            IF JSON_TYPE(JSON_EXTRACT(nested_value, '$.reason_code')) <> 'NULL' THEN
                SET safe_values = JSON_ARRAY_APPEND(
                    safe_values, '$', JSON_EXTRACT(nested_value, '$.reason_code')
                );
            END IF;
            SET nested_index = nested_index + 1;
        END WHILE;
        SET safe_index = 0;
        WHILE valid AND safe_index < JSON_LENGTH(safe_values) DO
            SET nested_value = JSON_EXTRACT(safe_values, CONCAT('$[', safe_index, ']'));
            SET text_value = JSON_UNQUOTE(nested_value);
            IF NOT COALESCE((
                JSON_TYPE(nested_value) = 'STRING' AND
                CHAR_LENGTH(text_value) BETWEEN 1 AND 255 AND
                NOT REGEXP_LIKE(
                    text_value COLLATE utf8mb4_bin,
                    CONCAT('[', CHAR(92 USING utf8mb4), 'p{C}]')
                ) AND
                NOT REGEXP_LIKE(
                    text_value COLLATE utf8mb4_bin,
                    CONCAT('^[', CHAR(92 USING utf8mb4), 'p{Z}',
                           CHAR(92 USING utf8mb4), 's]|[',
                           CHAR(92 USING utf8mb4), 'p{Z}',
                           CHAR(92 USING utf8mb4), 's]$')
                )
            ), FALSE) THEN SET valid = FALSE; END IF;
            SET safe_index = safe_index + 1;
        END WHILE;
        SET text_value = JSON_UNQUOTE(JSON_EXTRACT(current_value, '$.observed_at'));
        IF NOT COALESCE((
            JSON_TYPE(JSON_EXTRACT(current_value, '$.observed_at')) = 'STRING' AND
            REGEXP_LIKE(text_value COLLATE utf8mb4_bin, '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$', 'c') AND
            STR_TO_DATE(text_value, IF(INSTR(text_value, '.') > 0, '%Y-%m-%dT%H:%i:%s.%fZ', '%Y-%m-%dT%H:%i:%sZ')) IS NOT NULL
        ), FALSE) THEN SET valid = FALSE; END IF;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.next_verification_at')) NOT IN ('NULL', 'STRING') OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.execution_precondition')) NOT IN ('NULL', 'STRING') OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.superseded_by')) NOT IN ('NULL', 'STRING') OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')) NOT IN ('NULL', 'STRING') OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) NOT IN ('NULL', 'STRING') THEN
        SET valid = FALSE;
    END IF;
    SET safe_values = JSON_ARRAY(
        JSON_EXTRACT(p_proposal_data, '$.next_verification_at'),
        JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at'),
        JSON_EXTRACT(p_proposal_data, '$.erased_at')
    );
    SET safe_index = 0;
    WHILE valid AND safe_index < JSON_LENGTH(safe_values) DO
        SET nested_value = JSON_EXTRACT(safe_values, CONCAT('$[', safe_index, ']'));
        IF JSON_TYPE(nested_value) <> 'NULL' THEN
            SET text_value = JSON_UNQUOTE(nested_value);
            IF NOT COALESCE((
                JSON_TYPE(nested_value) = 'STRING' AND
                REGEXP_LIKE(
                    text_value COLLATE utf8mb4_bin,
                    '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$',
                    'c'
                ) AND
                STR_TO_DATE(
                    text_value,
                    IF(
                        INSTR(text_value, '.') > 0,
                        '%Y-%m-%dT%H:%i:%s.%fZ',
                        '%Y-%m-%dT%H:%i:%sZ'
                    )
                ) IS NOT NULL
            ), FALSE) THEN SET valid = FALSE; END IF;
        END IF;
        SET safe_index = safe_index + 1;
    END WHILE;
    IF JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) <> 'NULL' AND (
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.protected_private_snapshot')) <> 'NULL' OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.commitment')) <> 'NULL' OR
        JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.display_preview')) <> 0 OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.requesting_principal')) <> 'NULL' OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.proposing_agent')) <> 'NULL' OR
        JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.authority_evidence')) <> 0 OR
        JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.receipts')) <> 0 OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.safe_result')) <> 'NULL' OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.execution_precondition')) <> 'NULL' OR
        JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')) <> 'NULL'
    ) THEN SET valid = FALSE; END IF;
    IF NOT valid THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT =
            'proposal data violates the strict storage contract';
    END IF;
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_create_proposal;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_create_proposal(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_action_namespace TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_action_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_action_version INT UNSIGNED,
    IN p_semantic_effect_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_effect_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin,
    IN p_lifecycle_status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
    IN p_revision BIGINT UNSIGNED,
    IN p_created_at DATETIME(6),
    IN p_expires_at DATETIME(6),
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    CALL threvo_actions_validate_proposal_data(p_proposal_data);
    IF p_revision <> 0 OR p_lifecycle_status <> 'awaiting_authority' OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')) <> 'NULL' OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) <> 'NULL' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT =
            'new proposal violates the creation contract';
    END IF;
    INSERT INTO threvo_actions_proposals (
        tenant_reference, proposal_reference, action_namespace, action_name,
        action_version, semantic_effect_reference, effect_kind, lifecycle_status,
        revision, created_at, expires_at, proposal_data
    ) VALUES (
        p_tenant_reference, p_proposal_reference, p_action_namespace, p_action_name,
        p_action_version, p_semantic_effect_reference, p_effect_kind, p_lifecycle_status,
        p_revision, p_created_at, p_expires_at, p_proposal_data
    );
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_claim_effect;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_claim_effect(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_admitted_at DATETIME(6)
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    INSERT INTO threvo_actions_effect_claims (
        tenant_reference, effect_identity, action_namespace, action_name,
        action_version, semantic_effect_reference, proposal_reference, admitted_at
    )
    SELECT proposal.tenant_reference,
           UNHEX(SHA2(CONCAT(
               OCTET_LENGTH(proposal.action_namespace), ':', proposal.action_namespace,
               OCTET_LENGTH(proposal.action_name), ':', proposal.action_name,
               OCTET_LENGTH(CAST(proposal.action_version AS CHAR)), ':',
               proposal.action_version,
               OCTET_LENGTH(proposal.semantic_effect_reference), ':',
               proposal.semantic_effect_reference
           ), 256)),
           proposal.action_namespace,
           proposal.action_name,
           proposal.action_version,
           proposal.semantic_effect_reference,
           proposal.proposal_reference,
           p_admitted_at
    FROM threvo_actions_proposals AS proposal
    WHERE proposal.tenant_reference = p_tenant_reference
      AND proposal.proposal_reference = p_proposal_reference
      AND proposal.lifecycle_status = 'authorized'
      AND proposal.expires_at > GREATEST(p_admitted_at, UTC_TIMESTAMP(6))
      AND JSON_TYPE(JSON_EXTRACT(proposal.proposal_data, '$.erasure_pending_at')) = 'NULL'
      AND JSON_TYPE(JSON_EXTRACT(proposal.proposal_data, '$.erased_at')) = 'NULL';
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_runtime_update_proposal;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_runtime_update_proposal(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_expected_status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
    IN p_lifecycle_status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
    IN p_revision BIGINT UNSIGNED,
    IN p_expires_at DATETIME(6),
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    DECLARE old_evidence_length INT DEFAULT 0;
    DECLARE new_evidence_length INT DEFAULT 0;
    DECLARE old_receipt_length INT DEFAULT 0;
    DECLARE new_receipt_length INT DEFAULT 0;
    DECLARE sequence_index INT DEFAULT 0;
    DECLARE nested_index INT DEFAULT 0;
    DECLARE append_only BOOLEAN DEFAULT TRUE;

    CALL threvo_actions_validate_proposal_data(p_proposal_data);

    SET old_evidence_length = JSON_LENGTH(JSON_EXTRACT(
        (SELECT proposal_data FROM threvo_actions_proposals
          WHERE tenant_reference = p_tenant_reference
            AND proposal_reference = p_proposal_reference),
        '$.authority_evidence'
    ));
    SET new_evidence_length = JSON_LENGTH(JSON_EXTRACT(
        p_proposal_data, '$.authority_evidence'
    ));
    SET old_receipt_length = JSON_LENGTH(JSON_EXTRACT(
        (SELECT proposal_data FROM threvo_actions_proposals
          WHERE tenant_reference = p_tenant_reference
            AND proposal_reference = p_proposal_reference),
        '$.receipts'
    ));
    SET new_receipt_length = JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.receipts'));

    IF old_evidence_length IS NULL OR new_evidence_length IS NULL OR
       old_receipt_length IS NULL OR new_receipt_length IS NULL OR
       new_evidence_length < old_evidence_length OR
       new_receipt_length < old_receipt_length OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.authority_evidence')) <> 'ARRAY' OR
       JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.receipts')) <> 'ARRAY' THEN
        SET append_only = FALSE;
    END IF;

    WHILE append_only AND sequence_index < old_evidence_length DO
        IF NOT (
            JSON_EXTRACT(
                (SELECT proposal_data FROM threvo_actions_proposals
                  WHERE tenant_reference = p_tenant_reference
                    AND proposal_reference = p_proposal_reference),
                CONCAT('$.authority_evidence[', sequence_index, ']')
            ) <=> JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, ']')
            )
        ) THEN
            SET append_only = FALSE;
        END IF;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    SET sequence_index = old_evidence_length;
    WHILE append_only AND sequence_index < new_evidence_length DO
        IF NOT COALESCE((
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, ']')
            )) = 'OBJECT' AND
            JSON_LENGTH(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, ']')
            )) = 14 AND
            JSON_CONTAINS_PATH(
                p_proposal_data,
                'all',
                CONCAT('$.authority_evidence[', sequence_index, '].kind'),
                CONCAT('$.authority_evidence[', sequence_index, '].domain'),
                CONCAT('$.authority_evidence[', sequence_index, '].schema_version'),
                CONCAT('$.authority_evidence[', sequence_index, '].tenant_reference'),
                CONCAT('$.authority_evidence[', sequence_index, '].action_type'),
                CONCAT('$.authority_evidence[', sequence_index, '].proposal_instance_reference'),
                CONCAT('$.authority_evidence[', sequence_index, '].semantic_effect_reference'),
                CONCAT('$.authority_evidence[', sequence_index, '].authority'),
                CONCAT('$.authority_evidence[', sequence_index, '].audience'),
                CONCAT('$.authority_evidence[', sequence_index, '].decision'),
                CONCAT('$.authority_evidence[', sequence_index, '].proposal_commitment'),
                CONCAT('$.authority_evidence[', sequence_index, '].channel_assurance'),
                CONCAT('$.authority_evidence[', sequence_index, '].issued_at'),
                CONCAT('$.authority_evidence[', sequence_index, '].expires_at')
            ) = 1 AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].kind')
            )) = 'bound_decision' AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].domain')
            )) = 'threvo.actions.authority-evidence' AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].schema_version')
            )) = 'internal/v0' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type')
            )) = 'OBJECT' AND
            JSON_LENGTH(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type')
            )) = 3 AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].tenant_reference')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.namespace')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.name')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.version')
            )) IN ('INTEGER', 'UNSIGNED INTEGER') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT(
                    '$.authority_evidence[', sequence_index,
                    '].proposal_instance_reference'
                )
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT(
                    '$.authority_evidence[', sequence_index,
                    '].semantic_effect_reference'
                )
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].proposal_commitment')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].authority')
            )) = 'OBJECT' AND
            JSON_LENGTH(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].authority')
            )) = 2 AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].authority.kind')
            )) = 'confirming_authority' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].authority.reference')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].audience')
            )) = 'ARRAY' AND
            JSON_LENGTH(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].audience')
            )) > 0 AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].decision')
            )) IN ('approve', 'reject') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].channel_assurance')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].issued_at')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].expires_at')
            )) = 'STRING' AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].tenant_reference')
            )) <=> p_tenant_reference AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT(
                    '$.authority_evidence[', sequence_index,
                    '].proposal_instance_reference'
                )
            )) <=> p_proposal_reference AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.namespace')
            )) <=> (
                SELECT action_namespace FROM threvo_actions_proposals
                WHERE tenant_reference = p_tenant_reference
                  AND proposal_reference = p_proposal_reference
            ) AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.name')
            )) <=> (
                SELECT action_name FROM threvo_actions_proposals
                WHERE tenant_reference = p_tenant_reference
                  AND proposal_reference = p_proposal_reference
            ) AND
            CAST(JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].action_type.version')
            )) AS UNSIGNED) <=> (
                SELECT action_version FROM threvo_actions_proposals
                WHERE tenant_reference = p_tenant_reference
                  AND proposal_reference = p_proposal_reference
            ) AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT(
                    '$.authority_evidence[', sequence_index,
                    '].semantic_effect_reference'
                )
            )) <=> (
                SELECT semantic_effect_reference FROM threvo_actions_proposals
                WHERE tenant_reference = p_tenant_reference
                  AND proposal_reference = p_proposal_reference
            ) AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.authority_evidence[', sequence_index, '].proposal_commitment')
            )) <=> JSON_UNQUOTE(JSON_EXTRACT(
                (SELECT proposal_data FROM threvo_actions_proposals
                  WHERE tenant_reference = p_tenant_reference
                    AND proposal_reference = p_proposal_reference),
                '$.commitment.digest'
            ))
        ), FALSE) THEN
            SET append_only = FALSE;
        END IF;
        SET nested_index = 0;
        WHILE append_only AND nested_index < JSON_LENGTH(JSON_EXTRACT(
            p_proposal_data,
            CONCAT('$.authority_evidence[', sequence_index, '].audience')
        )) DO
            IF JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT(
                    '$.authority_evidence[', sequence_index,
                    '].audience[', nested_index, ']'
                )
            )) <> 'STRING' THEN
                SET append_only = FALSE;
            END IF;
            SET nested_index = nested_index + 1;
        END WHILE;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    SET sequence_index = 0;
    WHILE append_only AND sequence_index < old_receipt_length DO
        IF NOT (
            JSON_EXTRACT(
                (SELECT proposal_data FROM threvo_actions_proposals
                  WHERE tenant_reference = p_tenant_reference
                    AND proposal_reference = p_proposal_reference),
                CONCAT('$.receipts[', sequence_index, ']')
            ) <=> JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, ']')
            )
        ) THEN
            SET append_only = FALSE;
        END IF;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    SET sequence_index = old_receipt_length;
    WHILE append_only AND sequence_index < new_receipt_length DO
        IF NOT COALESCE((
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, ']')
            )) = 'OBJECT' AND
            JSON_CONTAINS_PATH(
                p_proposal_data,
                'all',
                CONCAT('$.receipts[', sequence_index, '].schema_version'),
                CONCAT('$.receipts[', sequence_index, '].receipt_reference'),
                CONCAT('$.receipts[', sequence_index, '].correlation_reference'),
                CONCAT('$.receipts[', sequence_index, '].causation_reference'),
                CONCAT('$.receipts[', sequence_index, '].observed_at'),
                CONCAT('$.receipts[', sequence_index, '].runtime_revision'),
                CONCAT('$.receipts[', sequence_index, '].external_reference'),
                CONCAT('$.receipts[', sequence_index, '].corrects_receipt_reference'),
                CONCAT('$.receipts[', sequence_index, '].supersedes_receipt_reference'),
                CONCAT('$.receipts[', sequence_index, '].reason_code'),
                CONCAT('$.receipts[', sequence_index, '].receipt_type'),
                CONCAT('$.receipts[', sequence_index, '].status')
            ) = 1 AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].schema_version')
            )) = 'internal/v0' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].receipt_reference')
            )) = 'STRING' AND
            JSON_UNQUOTE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].correlation_reference')
            )) <=> p_proposal_reference AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].causation_reference')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].observed_at')
            )) = 'STRING' AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].runtime_revision')
            )) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].external_reference')
            )) IN ('NULL', 'OBJECT') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].corrects_receipt_reference')
            )) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].supersedes_receipt_reference')
            )) IN ('NULL', 'STRING') AND
            JSON_TYPE(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].reason_code')
            )) IN ('NULL', 'STRING') AND
            (
                JSON_TYPE(JSON_EXTRACT(
                    p_proposal_data,
                    CONCAT('$.receipts[', sequence_index, '].external_reference')
                )) = 'NULL' OR (
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].external_reference')
                    )) = 2 AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].external_reference.system')
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].external_reference.reference')
                    )) = 'STRING'
                )
            ) AND (
                (
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].receipt_type')
                    )) = 'proposal' AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, ']')
                    )) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].status')
                    )) IN ('prepared', 'failed', 'missing') AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].requesting_principal')
                    )) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].requesting_principal.kind')
                    )) = 'requesting_principal' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].requesting_principal.reference')
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].proposing_agent')
                    )) IN ('NULL', 'OBJECT') AND
                    (
                        JSON_TYPE(JSON_EXTRACT(
                            p_proposal_data,
                            CONCAT('$.receipts[', sequence_index, '].proposing_agent')
                        )) = 'NULL' OR (
                            JSON_LENGTH(JSON_EXTRACT(
                                p_proposal_data,
                                CONCAT('$.receipts[', sequence_index, '].proposing_agent')
                            )) = 2 AND
                            JSON_UNQUOTE(JSON_EXTRACT(
                                p_proposal_data,
                                CONCAT('$.receipts[', sequence_index, '].proposing_agent.kind')
                            )) = 'proposing_agent' AND
                            JSON_TYPE(JSON_EXTRACT(
                                p_proposal_data,
                                CONCAT(
                                    '$.receipts[', sequence_index,
                                    '].proposing_agent.reference'
                                )
                            )) = 'STRING'
                        )
                    )
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].receipt_type')
                    )) = 'authority' AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, ']')
                    )) = 13 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].status')
                    )) IN ('recorded', 'rejected', 'failed', 'missing') AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant')
                    )) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.kind')
                    )) = 'confirming_authority' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.reference')
                    )) = 'STRING'
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].receipt_type')
                    )) = 'execution' AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, ']')
                    )) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].status')
                    )) IN (
                        'started', 'accepted', 'partially_succeeded',
                        'stale_no_effect', 'failed_known', 'failed_unknown', 'missing'
                    ) AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant')
                    )) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.kind')
                    )) = 'governed_executor' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.reference')
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].item_outcomes')
                    )) = 'ARRAY'
                ) OR (
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].receipt_type')
                    )) = 'verification' AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, ']')
                    )) = 14 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].status')
                    )) IN (
                        'verified_completion', 'verified_terminal_failure',
                        'provisional_absence', 'authoritative_final_absence',
                        'target_unavailable', 'verification_unresolved', 'missing'
                    ) AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant')
                    )) = 2 AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.kind')
                    )) = 'authoritative_target' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].participant.reference')
                    )) = 'STRING' AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT('$.receipts[', sequence_index, '].item_outcomes')
                    )) = 'ARRAY'
                )
            )
        ), FALSE) THEN
            SET append_only = FALSE;
        END IF;
        IF append_only AND JSON_UNQUOTE(JSON_EXTRACT(
            p_proposal_data,
            CONCAT('$.receipts[', sequence_index, '].receipt_type')
        )) IN ('execution', 'verification') THEN
            SET nested_index = 0;
            WHILE append_only AND nested_index < JSON_LENGTH(JSON_EXTRACT(
                p_proposal_data,
                CONCAT('$.receipts[', sequence_index, '].item_outcomes')
            )) DO
                IF NOT COALESCE((
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT(
                            '$.receipts[', sequence_index,
                            '].item_outcomes[', nested_index, ']'
                        )
                    )) = 'OBJECT' AND
                    JSON_LENGTH(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT(
                            '$.receipts[', sequence_index,
                            '].item_outcomes[', nested_index, ']'
                        )
                    )) = 3 AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT(
                            '$.receipts[', sequence_index,
                            '].item_outcomes[', nested_index, '].item_reference'
                        )
                    )) = 'STRING' AND
                    JSON_UNQUOTE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT(
                            '$.receipts[', sequence_index,
                            '].item_outcomes[', nested_index, '].status'
                        )
                    )) IN ('succeeded', 'failed_known', 'failed_unknown') AND
                    JSON_TYPE(JSON_EXTRACT(
                        p_proposal_data,
                        CONCAT(
                            '$.receipts[', sequence_index,
                            '].item_outcomes[', nested_index, '].reason_code'
                        )
                    )) IN ('NULL', 'STRING')
                ), FALSE) THEN
                    SET append_only = FALSE;
                END IF;
                SET nested_index = nested_index + 1;
            END WHILE;
        END IF;
        SET sequence_index = sequence_index + 1;
    END WHILE;

    UPDATE threvo_actions_proposals
       SET lifecycle_status = p_lifecycle_status,
           revision = p_revision,
           expires_at = p_expires_at,
           proposal_data = p_proposal_data
     WHERE tenant_reference = p_tenant_reference
       AND proposal_reference = p_proposal_reference
       AND revision = p_expected_revision
       AND lifecycle_status = p_expected_status
       AND append_only
       AND JSON_REMOVE(
           proposal_data,
           '$.lifecycle_status', '$.revision', '$.expires_at',
           '$.authority_evidence', '$.receipts',
           '$.verification_attempts', '$.next_verification_at', '$.safe_result',
           '$.execution_precondition', '$.superseded_by'
       ) <=> JSON_REMOVE(
           p_proposal_data,
           '$.lifecycle_status', '$.revision', '$.expires_at',
           '$.authority_evidence', '$.receipts',
           '$.verification_attempts', '$.next_verification_at', '$.safe_result',
           '$.execution_precondition', '$.superseded_by'
       );
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_transfer_effect_claim;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_transfer_effect_claim(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_effect_identity BINARY(32),
    IN p_current_owner_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_replacement_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_admitted_at DATETIME(6)
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    UPDATE threvo_actions_effect_claims AS claim
       SET proposal_reference = p_replacement_reference,
           admitted_at = p_admitted_at
     WHERE claim.tenant_reference = p_tenant_reference
       AND claim.effect_identity = p_effect_identity
       AND claim.proposal_reference = p_current_owner_reference
       AND EXISTS (
           SELECT 1 FROM threvo_actions_proposals AS owner
            WHERE owner.tenant_reference = claim.tenant_reference
              AND owner.proposal_reference = p_current_owner_reference
              AND owner.action_namespace = claim.action_namespace
              AND owner.action_name = claim.action_name
              AND owner.action_version = claim.action_version
              AND owner.semantic_effect_reference = claim.semantic_effect_reference
              AND owner.lifecycle_status IN ('failed_known', 'stale')
       )
       AND EXISTS (
           SELECT 1 FROM threvo_actions_proposals AS replacement
            WHERE replacement.tenant_reference = claim.tenant_reference
              AND replacement.proposal_reference = p_replacement_reference
              AND replacement.action_namespace = claim.action_namespace
              AND replacement.action_name = claim.action_name
              AND replacement.action_version = claim.action_version
              AND replacement.semantic_effect_reference = claim.semantic_effect_reference
              AND replacement.lifecycle_status = 'authorized'
              AND replacement.expires_at > GREATEST(p_admitted_at, UTC_TIMESTAMP(6))
              AND JSON_TYPE(JSON_EXTRACT(
                  replacement.proposal_data, '$.erasure_pending_at'
              )) = 'NULL'
              AND JSON_TYPE(JSON_EXTRACT(replacement.proposal_data, '$.erased_at')) = 'NULL'
       );
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_mark_erasure_pending;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_mark_erasure_pending(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    CALL threvo_actions_validate_proposal_data(p_proposal_data);
    UPDATE threvo_actions_proposals
       SET revision = p_expected_revision + 1,
           proposal_data = p_proposal_data
     WHERE tenant_reference = p_tenant_reference
       AND proposal_reference = p_proposal_reference
       AND revision = p_expected_revision
       AND lifecycle_status NOT IN ('executing', 'failed_unknown', 'verification_pending')
       AND JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erasure_pending_at')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erased_at')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')) <> 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) = 'NULL'
       AND JSON_REMOVE(proposal_data, '$.revision', '$.erasure_pending_at') <=>
           JSON_REMOVE(p_proposal_data, '$.revision', '$.erasure_pending_at');
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_complete_erasure;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_complete_erasure(
    IN p_tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    CALL threvo_actions_validate_proposal_data(p_proposal_data);
    UPDATE threvo_actions_proposals
       SET revision = p_expected_revision + 1,
           proposal_data = p_proposal_data
     WHERE tenant_reference = p_tenant_reference
       AND proposal_reference = p_proposal_reference
       AND revision = p_expected_revision
       AND JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erasure_pending_at')) <> 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(proposal_data, '$.erased_at')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) <> 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.protected_private_snapshot')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.commitment')) = 'NULL'
       AND JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.display_preview')) = 0
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.requesting_principal')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.proposing_agent')) = 'NULL'
       AND JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.authority_evidence')) = 0
       AND JSON_LENGTH(JSON_EXTRACT(p_proposal_data, '$.receipts')) = 0
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.safe_result')) = 'NULL'
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.execution_precondition')) = 'NULL'
       AND JSON_REMOVE(
           proposal_data,
           '$.revision', '$.protected_private_snapshot', '$.commitment',
           '$.display_preview', '$.requesting_principal', '$.proposing_agent',
           '$.authority_evidence', '$.receipts', '$.safe_result',
           '$.execution_precondition', '$.next_verification_at',
           '$.erasure_pending_at', '$.erased_at'
       ) <=> JSON_REMOVE(
           p_proposal_data,
           '$.revision', '$.protected_private_snapshot', '$.commitment',
           '$.display_preview', '$.requesting_principal', '$.proposing_agent',
           '$.authority_evidence', '$.receipts', '$.safe_result',
           '$.execution_precondition', '$.next_verification_at',
           '$.erasure_pending_at', '$.erased_at'
       );
    SELECT ROW_COUNT();
END;
