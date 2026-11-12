SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'capex.LlmShowcaseRun', N'U') IS NULL
    OR COL_LENGTH(N'capex.Assessment', N'TaskSuggestionID') IS NULL
    OR COL_LENGTH(N'task.TaskSuggestion', N'MatchMethod') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51192, 'Apply migrations 009 and 012 and create the approved runtime user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    -- Model recommendations are separate from review.BundleDecision human approvals.
    IF OBJECT_ID(N'capex.BundleDecision', N'U') IS NULL
        CREATE TABLE capex.BundleDecision
        (
            WorkflowRunId nvarchar(128) NOT NULL,
            PackageId nvarchar(128) NOT NULL,
            InvoiceId nvarchar(128) NOT NULL,
            InvoiceLineId nvarchar(256) NOT NULL,
            BundleGroupId nvarchar(128) NULL,
            ModelDecision varchar(16) NOT NULL,
            IsOneAssetSuggestion bit NULL,
            Confidence float NOT NULL,
            Rationale nvarchar(max) NOT NULL,
            EvidenceRefs nvarchar(max) NOT NULL,
            UsedLlm bit NOT NULL,
            ModelVersion nvarchar(128) NOT NULL,
            PromptVersion nvarchar(128) NOT NULL,
            CreatedAtUtc datetime2(3) NOT NULL,
            CONSTRAINT PK_GptBundleDecision PRIMARY KEY NONCLUSTERED (WorkflowRunId, PackageId, InvoiceLineId),
            CONSTRAINT FK_GptBundleDecision_Assessment FOREIGN KEY (WorkflowRunId, PackageId, InvoiceLineId)
                REFERENCES capex.Assessment (WorkflowRunId, PackageId, LineId),
            CONSTRAINT FK_GptBundleDecision_Group FOREIGN KEY (WorkflowRunId, BundleGroupId)
                REFERENCES capex.BundleCandidateGroup (WorkflowRunId, BundleGroupId),
            CONSTRAINT CK_GptBundleDecision_Result CHECK (ModelDecision IN ('BUNDLE', 'NO_BUNDLE', 'REVIEW')),
            CONSTRAINT CK_GptBundleDecision_Confidence CHECK (Confidence >= 0 AND Confidence <= 1),
            CONSTRAINT CK_GptBundleDecision_Evidence CHECK (ISJSON(EvidenceRefs) = 1),
            CONSTRAINT CK_GptBundleDecision_Model CHECK (UsedLlm = 1 AND ModelVersion = N'gpt-5.1'),
            CONSTRAINT CK_GptBundleDecision_Group CHECK (
                (ModelDecision = 'BUNDLE' AND BundleGroupId IS NOT NULL
                    AND IsOneAssetSuggestion IS NOT NULL AND IsOneAssetSuggestion = 1)
                OR (ModelDecision = 'NO_BUNDLE' AND BundleGroupId IS NULL
                    AND IsOneAssetSuggestion IS NOT NULL AND IsOneAssetSuggestion = 0)
                OR (ModelDecision = 'REVIEW' AND BundleGroupId IS NULL AND IsOneAssetSuggestion IS NULL)
            )
        );
    GRANT SELECT, INSERT ON OBJECT::capex.BundleDecision TO [cict-ai-cus-capex-id];
    GRANT UPDATE (MatchMethod, ItemIdentified, UnresolvedReason, ContractVersion)
        ON OBJECT::task.TaskSuggestion TO [cict-ai-cus-capex-id];
    GRANT UPDATE (TaskSuggestionID) ON OBJECT::capex.Assessment TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT N'GPT_BUNDLE_DECISION_STORE_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
