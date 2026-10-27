-- Gitea 28.1.0's documented owner/repository Actions permission ceiling.
-- The public REST API does not expose this setting in that pinned release.
-- Touch ONLY the dedicated Bot's one managed repository, preserving all other
-- users, repositories, unit configuration and signing/authentication material.
BEGIN IMMEDIATE;
CREATE TEMP TABLE ci_assert(ok INTEGER CHECK(ok=1));
INSERT INTO ci_assert SELECT count(*)=1 FROM "user" WHERE lower_name='mag-ci' AND type=4 AND is_admin=0;
INSERT INTO ci_assert SELECT count(*)=1 FROM repository WHERE owner_id=(SELECT id FROM "user" WHERE lower_name='mag-ci');
INSERT INTO ci_assert SELECT count(*)=1 FROM repository WHERE owner_id=(SELECT id FROM "user" WHERE lower_name='mag-ci') AND lower_name='mag-portable-stack';
INSERT INTO ci_assert SELECT count(*)=1 FROM repo_unit WHERE type=10 AND repo_id=(SELECT r.id FROM repository r JOIN "user" u ON u.id=r.owner_id WHERE u.lower_name='mag-ci' AND r.lower_name='mag-portable-stack');
INSERT INTO ci_assert SELECT count(*)=0 FROM user_setting WHERE user_id=(SELECT id FROM "user" WHERE lower_name='mag-ci') AND setting_key='actions.config' AND (NOT json_valid(setting_value) OR json_type(setting_value)!='object');
UPDATE user_setting SET setting_value=json_set(setting_value,
 '$.token_permission_mode','restricted', '$.max_token_permissions',json('{"unit_access_modes":{"1":1}}'), '$.allowed_cross_repo_ids',json('[]'))
 WHERE user_id=(SELECT id FROM "user" WHERE lower_name='mag-ci') AND setting_key='actions.config';
INSERT INTO user_setting(user_id,setting_key,setting_value)
 SELECT id,'actions.config','{"token_permission_mode":"restricted","max_token_permissions":{"unit_access_modes":{"1":1}},"allowed_cross_repo_ids":[]}' FROM "user" WHERE lower_name='mag-ci'
 AND NOT EXISTS(SELECT 1 FROM user_setting WHERE user_id="user".id AND setting_key='actions.config');
UPDATE repo_unit SET config=json_set(CASE WHEN config='' THEN '{}' ELSE config END,
 '$.token_permission_mode','restricted', '$.max_token_permissions',json('{"unit_access_modes":{"1":1}}'), '$.override_owner_config',json('false'), '$.CollaborativeOwnerIDs',json('[]'))
 WHERE type=10 AND repo_id=(SELECT r.id FROM repository r JOIN "user" u ON u.id=r.owner_id WHERE u.lower_name='mag-ci' AND r.lower_name='mag-portable-stack');
COMMIT;
