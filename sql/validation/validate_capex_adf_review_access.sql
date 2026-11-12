-- Read-only DBA check; run in cict-ai-cus-sqldb with metadata visibility.
SET NOCOUNT ON;

IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51230, 'Run this check in cict-ai-cus-sqldb.', 1;

SELECT DB_NAME() AS DatabaseName, USER_NAME() AS ConnectedDatabaseUser,
       OBJECT_ID(N'mvp_review.imports', N'U') AS ImportsTableId,
       OBJECT_ID(N'mvp_review.documents', N'U') AS DocumentsTableId,
       OBJECT_ID(N'mvp_review.decisions', N'U') AS DecisionsTableId,
       DATABASE_PRINCIPAL_ID(N'capex_mvp_review_runtime') AS ReviewRoleId,
       DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') AS WorkerUserId,
       IS_ROLEMEMBER(N'capex_mvp_review_runtime', N'cict-ai-cus-capex-id') AS WorkerInReviewRole;

SELECT expected.ObjectName,
       CASE WHEN OBJECT_ID(expected.ObjectName, N'U') IS NULL
            THEN N'MISSING_OR_NOT_VISIBLE' ELSE N'PRESENT' END AS TableStatus
FROM (VALUES (N'mvp_review.imports'), (N'mvp_review.documents'),
             (N'mvp_review.decisions')) AS expected(ObjectName);

SELECT name AS RuntimeRole,
       CASE WHEN EXISTS (
           SELECT 1 FROM sys.database_principals AS principal
           WHERE principal.name = expected.name AND principal.type = 'R'
       ) THEN N'PRESENT'
       WHEN DATABASE_PRINCIPAL_ID(name) IS NOT NULL THEN N'NAME_EXISTS_BUT_NOT_ROLE'
       ELSE N'MISSING_OR_NOT_VISIBLE' END AS RoleStatus
FROM (VALUES (N'capex_mvp_review_runtime')) AS expected(name);

SELECT name AS DatabasePrincipal, type_desc, authentication_type_desc,
       CONVERT(varchar(130), sid, 1) AS PrincipalSid
FROM sys.database_principals
WHERE name = N'cict-ai-cus-capex-id'
   OR sid IN (
       CONVERT(varbinary(16), CONVERT(uniqueidentifier,
           N'cf7d7929-5beb-426b-bd19-493c5c0c506d')),
       CONVERT(varbinary(16), CONVERT(uniqueidentifier,
           N'562512ef-d515-428a-8df5-6c4c589812ef'))
   );

SELECT role.name AS RuntimeRole, member.name AS RuntimeIdentity
FROM sys.database_role_members AS membership
JOIN sys.database_principals AS role ON role.principal_id = membership.role_principal_id
JOIN sys.database_principals AS member ON member.principal_id = membership.member_principal_id
WHERE role.name = N'capex_mvp_review_runtime';

SELECT principal.name AS Grantee, permission.permission_name, permission.state_desc,
       permission.class_desc, permission.major_id
FROM sys.database_permissions AS permission
JOIN sys.database_principals AS principal ON principal.principal_id = permission.grantee_principal_id
WHERE principal.name IN (N'capex_mvp_review_runtime', N'cict-ai-cus-capex-id')
ORDER BY principal.name, permission.class, permission.major_id, permission.permission_name;
