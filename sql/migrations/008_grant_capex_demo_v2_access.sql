SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51080, 'Create the approved cict-ai-cus-capex-id Entra contained user in this database before granting access.', 1;

IF OBJECT_ID(N'task.TaskSuggestion', N'U') IS NULL
    OR OBJECT_ID(N'pub.v1_TaskMaster', N'V') IS NULL
    OR OBJECT_ID(N'capex.AssessmentRun', N'U') IS NULL
    OR OBJECT_ID(N'capex.Assessment', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateGroup', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateMember', N'U') IS NULL
    OR OBJECT_ID(N'pub.v1_Assessment', N'V') IS NULL
    THROW 51081, 'Apply prerequisite migrations 004 through 007 before granting Demo V2 access.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    GRANT CONNECT TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::pub.v1_TaskMaster TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::pub.v1_Assessment TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::task.TaskSuggestion TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::capex.AssessmentRun TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::capex.Assessment TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::capex.BundleCandidateGroup TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::capex.BundleCandidateMember TO [cict-ai-cus-capex-id];

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
