-- Explicit shared-identity demo exception, not the distinct-identity production design.
-- Execute as an authorized DBA. Does not create users, roles or tables.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51231, 'Run this script in cict-ai-cus-sqldb.', 1;

IF OBJECT_ID(N'mvp_review.imports', N'U') IS NULL
    OR OBJECT_ID(N'mvp_review.documents', N'U') IS NULL
    OR OBJECT_ID(N'mvp_review.decisions', N'U') IS NULL
    THROW 51232, 'Existing review tables are missing or not visible. Stop for DBA review.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals
    WHERE name = N'capex_mvp_review_runtime' AND type = 'R'
)
    THROW 51233, 'Expected review database ROLE is missing, not visible, or has a different principal type.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals
    WHERE name = N'cict-ai-cus-capex-id'
      AND type = 'E'
      -- The existing demo user stores the UAMI client ID as its SID.
      AND sid IN (
          CONVERT(varbinary(16), CONVERT(uniqueidentifier,
              N'cf7d7929-5beb-426b-bd19-493c5c0c506d')),
          CONVERT(varbinary(16), CONVERT(uniqueidentifier,
              N'562512ef-d515-428a-8df5-6c4c589812ef'))
      )
)
    THROW 51234, 'Worker database principal does not match the approved UAMI object or client ID.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF IS_ROLEMEMBER(N'capex_mvp_review_runtime', N'cict-ai-cus-capex-id') = 0
        ALTER ROLE capex_mvp_review_runtime ADD MEMBER [cict-ai-cus-capex-id];

    IF ISNULL(IS_ROLEMEMBER(N'capex_mvp_review_runtime', N'cict-ai-cus-capex-id'), 0) <> 1
        THROW 51235, 'Worker review role membership could not be verified.', 1;
    COMMIT TRANSACTION;

    SELECT DB_NAME() AS DatabaseName,
           N'capex_mvp_review_runtime' AS RuntimeRole,
           N'cict-ai-cus-capex-id' AS RuntimeIdentity,
           IS_ROLEMEMBER(N'capex_mvp_review_runtime', N'cict-ai-cus-capex-id') AS WorkerInReviewRole,
           N'ADF_REVIEW_MEMBERSHIP_READY' AS Result;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
