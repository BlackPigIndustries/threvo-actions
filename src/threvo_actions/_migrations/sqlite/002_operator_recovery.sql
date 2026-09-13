DROP TRIGGER IF EXISTS enforce_proposal_update;

CREATE TRIGGER enforce_proposal_update
BEFORE UPDATE ON proposals
BEGIN
    SELECT CASE WHEN
        NEW.tenant_reference IS NOT OLD.tenant_reference OR
        NEW.proposal_reference IS NOT OLD.proposal_reference OR
        NEW.action_namespace IS NOT OLD.action_namespace OR
        NEW.action_name IS NOT OLD.action_name OR
        NEW.action_version IS NOT OLD.action_version OR
        NEW.semantic_effect_reference IS NOT OLD.semantic_effect_reference OR
        NEW.effect_kind IS NOT OLD.effect_kind OR
        NEW.created_at IS NOT OLD.created_at
    THEN RAISE(ABORT, 'proposal identity cannot change') END;

    SELECT CASE WHEN NEW.revision <> OLD.revision + 1
    THEN RAISE(ABORT, 'proposal revision must advance by one') END;

    SELECT CASE WHEN NEW.lifecycle_status <> OLD.lifecycle_status AND NOT (
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'denied') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'blocked') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'stale' AND
            NEW.lifecycle_status = 'superseded') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'failed_unknown' AND
            NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'verification_unresolved') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'partially_succeeded') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'verified') OR
        (OLD.lifecycle_status = 'verification_unresolved' AND
            NEW.lifecycle_status = 'verification_pending')
    ) THEN RAISE(ABORT, 'invalid proposal lifecycle transition') END;
END;
