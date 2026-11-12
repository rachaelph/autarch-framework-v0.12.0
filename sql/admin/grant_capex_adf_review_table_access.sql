-- Shared-identity demo alternative to review-role membership.
-- Execute with authority to grant table permissions. Does not create objects.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51236, 'Run this script in cict-ai-cus-sqldb.', 1;

IF OBJECT_ID(N'mvp_review.imports', N'U') IS NULL
    OR OBJECT_ID(N'mvp_review.documents', N'U') IS NULL
    OR OBJECT_ID(N'mvp_review.decisions', N'U') IS NULL
    THROW 51237, 'Existing review tables are missing or not visible. Stop for DBA review.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals
    WHERE name = N'cict-ai-cus-capex-id'
      AND type = 'E'
      AND sid IN (
          CONVERT(varbinary(16), CONVERT(uniqueidentifier,
              N'cf7d7929-5beb-426b-bd19-493c5c0c506d')),
          CONVERT(varbinary(16), CONVERT(uniqueidentifier,
              N'562512ef-d515-428a-8df5-6c4c589812ef'))
      )
)
    THROW 51238, 'Worker database principal does not match the approved UAMI object or client ID.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    GRANT SELECT, INSERT ON OBJECT::mvp_review.imports
        TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::mvp_review.documents
        TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::mvp_review.decisions
        TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;

    SELECT DB_NAME() AS DatabaseName,
           N'cict-ai-cus-capex-id' AS RuntimeIdentity,
           N'ADF_REVIEW_TABLE_GRANTS_APPLIED' AS Result,
           N'Repeat worker effective-access preflight before processing.' AS NextStep;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
