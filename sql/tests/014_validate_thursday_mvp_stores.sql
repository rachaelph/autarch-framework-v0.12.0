-- Read-only DBA verification after migration 014. No demo rows are inserted.
SET NOCOUNT ON;

IF (SELECT COUNT(*) FROM sys.tables AS t JOIN sys.schemas AS s ON s.schema_id = t.schema_id
    WHERE (s.name = N'mvp_batch' AND t.name IN (N'runs', N'checkpoints', N'attempts'))
       OR (s.name = N'mvp_review' AND t.name IN (N'imports', N'documents', N'decisions'))) <> 6
    THROW 51214, 'Migration 014 is incomplete: expected six isolated MVP tables.', 1;

IF ISNULL(COL_LENGTH(N'mvp_batch.runs', N'input_sha'), 0) <> 64
    OR ISNULL(COL_LENGTH(N'mvp_batch.checkpoints', N'payload'), 0) <> -1
    OR ISNULL(COL_LENGTH(N'mvp_review.documents', N'content'), 0) <> -1
    OR ISNULL(COL_LENGTH(N'mvp_review.decisions', N'previous_sha'), 0) <> 64
    THROW 51215, 'MVP store columns do not match the application contract.', 1;

IF DATABASE_PRINCIPAL_ID(N'capex_mvp_batch_runtime') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'capex_mvp_review_runtime') IS NULL
    THROW 51216, 'MVP runtime roles are missing.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.database_permissions
    WHERE grantee_principal_id = DATABASE_PRINCIPAL_ID(N'capex_mvp_review_runtime')
      AND class = 3 AND major_id = SCHEMA_ID(N'mvp_review')
      AND permission_name = N'INSERT' AND state = 'G'
)
    THROW 51217, 'Review runtime role is missing its insert grant.', 1;

SELECT N'THURSDAY_MVP_SCHEMA_VERIFIED' AS ValidationResult;
SELECT principal.name AS RuntimeRole, permission.permission_name, permission.state_desc,
       permission.class_desc, permission.major_id
FROM sys.database_permissions AS permission
JOIN sys.database_principals AS principal ON principal.principal_id = permission.grantee_principal_id
WHERE principal.name IN (N'capex_mvp_batch_runtime', N'capex_mvp_review_runtime')
ORDER BY principal.name, permission.class, permission.major_id, permission.permission_name;

-- Review this membership grid for distinct approved identities and no broad dbo roles.
SELECT role.name AS RuntimeRole, member.name AS RuntimeIdentity
FROM sys.database_role_members AS membership
JOIN sys.database_principals AS role ON role.principal_id = membership.role_principal_id
JOIN sys.database_principals AS member ON member.principal_id = membership.member_principal_id
WHERE role.name IN (N'capex_mvp_batch_runtime', N'capex_mvp_review_runtime')
ORDER BY role.name, member.name;
