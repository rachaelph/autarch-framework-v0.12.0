SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @WorkflowRunId nvarchar(128) =
        CONCAT(N'sql-verification-', CONVERT(nvarchar(36), NEWID()));
    DECLARE @PackageId nvarchar(128) = N'package-verification';
    DECLARE @InvoiceId nvarchar(128) = N'invoice-verification';
    DECLARE @BundleGroupId nvarchar(128) = N'bundle-verification';

    INSERT INTO capex.AssessmentRun
    (
        WorkflowRunId,
        SchemaVersion,
        ResultSha256,
        AssessmentCount,
        BundleGroupCount,
        BundleMemberCount,
        CreatedAtUtc
    )
    VALUES
    (
        @WorkflowRunId,
        '2.0',
        REPLICATE('a', 64),
        2,
        1,
        2,
        SYSUTCDATETIME()
    );

    INSERT INTO capex.Assessment
    (
        WorkflowRunId,
        SchemaVersion,
        PackageId,
        InvoiceId,
        LineId,
        SuggestedTaskCode,
        NatureResult,
        RepairMaintenanceIndicator,
        NeverCapitalizeHit,
        UnitCostMeetsThreshold,
        ThresholdAmount,
        ThresholdCurrency,
        Recommendation,
        OutcomeCategory,
        BundlingDisposition,
        BundleGroupId,
        MissingInputs,
        ReviewReasons,
        Rationale,
        PolicyCitations,
        UsedLlm,
        ModelVersion,
        PolicyVersion,
        CreatedAtUtc
    )
    VALUES
    (
        @WorkflowRunId,
        '2.0',
        @PackageId,
        @InvoiceId,
        N'line-1',
        N'5225',
        'CAPITALIZABLE',
        0,
        0,
        0,
        1000,
        'USD',
        'OPEX',
        N'OPEX_POTENTIAL_FUTURE_BUNDLING',
        'GROUPED',
        @BundleGroupId,
        N'[]',
        N'[]',
        N'Below threshold; grouped for analyst review.',
        N'["policy:verification"]',
        0,
        NULL,
        N'policy-verification',
        SYSUTCDATETIME()
    ),
    (
        @WorkflowRunId,
        '2.0',
        @PackageId,
        @InvoiceId,
        N'line-2',
        N'5225',
        'CAPITALIZABLE',
        0,
        0,
        0,
        1000,
        'USD',
        'OPEX',
        N'OPEX_POTENTIAL_FUTURE_BUNDLING',
        'GROUPED',
        @BundleGroupId,
        N'[]',
        N'[]',
        N'Below threshold; grouped for analyst review.',
        N'["policy:verification"]',
        0,
        NULL,
        N'policy-verification',
        SYSUTCDATETIME()
    );

    INSERT INTO capex.BundleCandidateGroup
    (
        WorkflowRunId,
        SchemaVersion,
        BundleGroupId,
        SuggestedTaskCode,
        SiteId,
        ProjectId,
        Currency,
        TotalAmount,
        ThresholdAmount,
        Disposition,
        MissingInputs,
        Rationale,
        PolicyCitations,
        PolicyVersion,
        CreatedAtUtc
    )
    VALUES
    (
        @WorkflowRunId,
        '2.0',
        @BundleGroupId,
        N'5225',
        N'DEMO-SITE-001',
        N'DEMO-PROJECT-001',
        'USD',
        1000,
        1000,
        'CANDIDATE',
        N'[]',
        N'Two matching lines reach the governed threshold.',
        N'["policy:verification"]',
        N'policy-verification',
        SYSUTCDATETIME()
    );

    INSERT INTO capex.BundleCandidateMember
    (
        WorkflowRunId,
        BundleGroupId,
        PackageId,
        InvoiceId,
        LineId,
        Quantity,
        UnitCost,
        LineAmount
    )
    VALUES
    (
        @WorkflowRunId,
        @BundleGroupId,
        @PackageId,
        @InvoiceId,
        N'line-1',
        1,
        600,
        600
    ),
    (
        @WorkflowRunId,
        @BundleGroupId,
        @PackageId,
        @InvoiceId,
        N'line-2',
        2,
        200,
        400
    );

    IF
    (
        SELECT COUNT(*)
        FROM pub.v1_Assessment
        WHERE workflowRunId = @WorkflowRunId
    ) <> 2
        THROW 51070, 'Published assessment row count is incorrect.', 1;

    IF EXISTS
    (
        SELECT 1
        FROM pub.v1_Assessment
        WHERE
            workflowRunId = @WorkflowRunId
            AND
            (
                bundleGroupId <> @BundleGroupId
                OR bundleTotalAmount <> 1000
                OR bundleMemberCount <> 2
                OR usedLlm <> 0
            )
    )
        THROW 51071, 'Published assessment bundle context is incorrect.', 1;

    IF
    (
        SELECT SUM(LineAmount)
        FROM capex.BundleCandidateMember
        WHERE
            WorkflowRunId = @WorkflowRunId
            AND BundleGroupId = @BundleGroupId
    ) <> 1000
        THROW 51072, 'Bundle member total does not reconcile.', 1;

    ROLLBACK TRANSACTION;
    PRINT 'CapEx assessment store verification passed.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
