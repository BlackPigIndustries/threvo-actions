CREATE TABLE IF NOT EXISTS threvo_actions_proposals (
    tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    action_namespace TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    action_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    action_version INT UNSIGNED NOT NULL,
    semantic_effect_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    effect_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lifecycle_status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    proposal_data JSON NOT NULL,
    PRIMARY KEY (tenant_reference, proposal_reference),
    CONSTRAINT threvo_actions_action_version_positive CHECK (action_version > 0),
    CONSTRAINT threvo_actions_effect_kind_current CHECK (effect_kind IN ('single', 'itemized')),
    CONSTRAINT threvo_actions_lifecycle_current CHECK (
        lifecycle_status IN (
            'awaiting_authority', 'denied', 'expired', 'authorized', 'blocked',
            'stale', 'superseded', 'executing', 'failed_known', 'failed_unknown',
            'verification_pending', 'verification_unresolved',
            'partially_succeeded', 'verified'
        )
    ),
    CONSTRAINT threvo_actions_expiry_order CHECK (expires_at > created_at),
    CONSTRAINT threvo_actions_json_tenant CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.tenant_reference')) = tenant_reference
    ),
    CONSTRAINT threvo_actions_json_proposal CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.proposal_reference')) = proposal_reference
    ),
    CONSTRAINT threvo_actions_json_namespace CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.action_type.namespace')) = action_namespace
    ),
    CONSTRAINT threvo_actions_json_name CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.action_type.name')) = action_name
    ),
    CONSTRAINT threvo_actions_json_version CHECK (
        CAST(JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.action_type.version')) AS UNSIGNED) = action_version
    ),
    CONSTRAINT threvo_actions_json_effect CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.semantic_effect_reference')) = semantic_effect_reference
    ),
    CONSTRAINT threvo_actions_json_effect_kind CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.effect_kind')) = effect_kind
    ),
    CONSTRAINT threvo_actions_json_lifecycle CHECK (
        JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.lifecycle_status')) = lifecycle_status
    ),
    CONSTRAINT threvo_actions_json_revision CHECK (
        CAST(JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.revision')) AS UNSIGNED) = revision
    ),
    INDEX threvo_actions_proposals_lifecycle_idx (lifecycle_status)
) ENGINE=InnoDB;
-- threvo-actions:next
CREATE TABLE IF NOT EXISTS threvo_actions_effect_claims (
    tenant_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    effect_identity BINARY(32) NOT NULL,
    action_namespace TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    action_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    action_version INT UNSIGNED NOT NULL,
    semantic_effect_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    proposal_reference VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    admitted_at DATETIME(6) NOT NULL,
    PRIMARY KEY (tenant_reference, effect_identity),
    CONSTRAINT threvo_actions_effect_proposal_fk FOREIGN KEY (
        tenant_reference, proposal_reference
    ) REFERENCES threvo_actions_proposals (tenant_reference, proposal_reference)
) ENGINE=InnoDB;
-- threvo-actions:next
DROP TRIGGER IF EXISTS threvo_actions_enforce_proposal_update;
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
DROP PROCEDURE IF EXISTS threvo_actions_runtime_update_proposal;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_runtime_update_proposal(
    IN p_tenant_reference VARCHAR(255),
    IN p_proposal_reference VARCHAR(255),
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_expected_status VARCHAR(32),
    IN p_lifecycle_status VARCHAR(32),
    IN p_revision BIGINT UNSIGNED,
    IN p_expires_at DATETIME(6),
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
    UPDATE threvo_actions_proposals
       SET lifecycle_status = p_lifecycle_status,
           revision = p_revision,
           expires_at = p_expires_at,
           proposal_data = p_proposal_data
     WHERE tenant_reference = p_tenant_reference
       AND proposal_reference = p_proposal_reference
       AND revision = p_expected_revision
       AND lifecycle_status = p_expected_status
       AND JSON_EXTRACT(proposal_data, '$.erasure_pending_at') <=>
           JSON_EXTRACT(p_proposal_data, '$.erasure_pending_at')
       AND JSON_EXTRACT(proposal_data, '$.erased_at') <=>
           JSON_EXTRACT(p_proposal_data, '$.erased_at');
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_transfer_effect_claim;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_transfer_effect_claim(
    IN p_tenant_reference VARCHAR(255),
    IN p_effect_identity BINARY(32),
    IN p_current_owner_reference VARCHAR(255),
    IN p_replacement_reference VARCHAR(255),
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
            WHERE owner.tenant_reference = p_tenant_reference
              AND owner.proposal_reference = p_current_owner_reference
              AND owner.lifecycle_status IN ('failed_known', 'stale')
       )
       AND EXISTS (
           SELECT 1 FROM threvo_actions_proposals AS replacement
            WHERE replacement.tenant_reference = p_tenant_reference
              AND replacement.proposal_reference = p_replacement_reference
              AND replacement.lifecycle_status = 'authorized'
              AND replacement.expires_at > UTC_TIMESTAMP(6)
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
    IN p_tenant_reference VARCHAR(255),
    IN p_proposal_reference VARCHAR(255),
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
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
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.erased_at')) = 'NULL';
    SELECT ROW_COUNT();
END;
-- threvo-actions:next
DROP PROCEDURE IF EXISTS threvo_actions_complete_erasure;
-- threvo-actions:next
CREATE PROCEDURE threvo_actions_complete_erasure(
    IN p_tenant_reference VARCHAR(255),
    IN p_proposal_reference VARCHAR(255),
    IN p_expected_revision BIGINT UNSIGNED,
    IN p_proposal_data JSON
)
SQL SECURITY DEFINER
MODIFIES SQL DATA
BEGIN
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
       AND JSON_TYPE(JSON_EXTRACT(p_proposal_data, '$.execution_precondition')) = 'NULL';
    SELECT ROW_COUNT();
END;
