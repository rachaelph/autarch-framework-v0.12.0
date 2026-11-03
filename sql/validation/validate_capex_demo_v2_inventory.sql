SET NOCOUNT ON;

SELECT
    DB_NAME() AS databaseName,
    SUSER_SNAME() AS loginName,
    USER_NAME() AS databaseUser,
    SYSUTCDATETIME() AS checkedAtUtc;

SELECT
    inventory.objectName,
    inventory.objectType,
    CASE
        WHEN OBJECT_ID(inventory.objectName, inventory.objectType) IS NULL
            THEN 'MISSING'
        ELSE 'PRESENT'
    END AS deploymentStatus
FROM
(
    VALUES
        (N'prep.InvoicePackage', N'U'),
        (N'prep.InvoiceLine', N'U'),
        (N'prep.InvoiceDataQualityWarning', N'U'),
        (N'prep.InvoiceOutbox', N'U'),
        (N'pub.v1_InvoicePackage', N'V'),
        (N'ref.TaskMaster', N'U'),
        (N'pub.v1_TaskMaster', N'V'),
        (N'task.TaskSuggestion', N'U'),
        (N'capex.AssessmentRun', N'U'),
        (N'capex.Assessment', N'U'),
        (N'capex.BundleCandidateGroup', N'U'),
        (N'capex.BundleCandidateMember', N'U'),
        (N'pub.v1_Assessment', N'V')
) AS inventory(objectName, objectType)
ORDER BY inventory.objectName;

IF OBJECT_ID(N'prep.InvoicePackage', N'U') IS NOT NULL
BEGIN
    SELECT 'prep.InvoicePackage' AS objectName, COUNT_BIG(*) AS rowCount
    FROM prep.InvoicePackage;

    SELECT
        PackageId,
        RunId,
        InvoiceId,
        InvoiceNumber,
        VendorName,
        Currency,
        InvoiceTotal,
        PackageStatus,
        SourceSha256,
        CreatedAtUtc
    FROM prep.InvoicePackage
    ORDER BY InsertedAtUtc DESC;
END;

IF OBJECT_ID(N'prep.InvoiceLine', N'U') IS NOT NULL
BEGIN
    SELECT 'prep.InvoiceLine' AS objectName, COUNT_BIG(*) AS rowCount
    FROM prep.InvoiceLine;

    SELECT
        PackageId,
        InvoiceLineId,
        LineNumber,
        Description,
        Quantity,
        UnitCost,
        LineAmount,
        PoTaskCode,
        AfeProjectId,
        SiteId
    FROM prep.InvoiceLine
    ORDER BY PackageId, LineNumber;
END;

IF OBJECT_ID(N'prep.InvoiceDataQualityWarning', N'U') IS NOT NULL
BEGIN
    SELECT
        'prep.InvoiceDataQualityWarning' AS objectName,
        COUNT_BIG(*) AS rowCount
    FROM prep.InvoiceDataQualityWarning;

    SELECT PackageId, WarningOrdinal, WarningText
    FROM prep.InvoiceDataQualityWarning
    ORDER BY PackageId, WarningOrdinal;
END;

IF OBJECT_ID(N'prep.InvoiceOutbox', N'U') IS NOT NULL
BEGIN
    SELECT 'prep.InvoiceOutbox' AS objectName, COUNT_BIG(*) AS rowCount
    FROM prep.InvoiceOutbox;

    SELECT
        EventId,
        PackageId,
        EventType,
        EventVersion,
        CorrelationId,
        OutboxStatus,
        AttemptCount,
        PublishedAtUtc,
        LastError
    FROM prep.InvoiceOutbox
    ORDER BY InsertedAtUtc DESC;
END;

IF OBJECT_ID(N'pub.v1_InvoicePackage', N'V') IS NOT NULL
BEGIN
    SELECT 'pub.v1_InvoicePackage' AS objectName, COUNT_BIG(*) AS rowCount
    FROM pub.v1_InvoicePackage;
END;

IF OBJECT_ID(N'ref.TaskMaster', N'U') IS NOT NULL
BEGIN
    SELECT
        'ref.TaskMaster' AS objectName,
        COUNT_BIG(*) AS allRows,
        SUM(CASE WHEN isCurrent = 1 AND active = 1 THEN 1 ELSE 0 END)
            AS currentActiveRows,
        COUNT(DISTINCT CASE
            WHEN isCurrent = 1 AND active = 1 THEN taskCode
        END) AS currentActiveTaskCodes
    FROM ref.TaskMaster;

    SELECT
        regionCode,
        taskCode,
        description,
        assetCategoryMajor,
        assetCategoryMinor,
        sourceVersion,
        sourceSha256
    FROM ref.TaskMaster
    WHERE isCurrent = 1
      AND active = 1
      AND taskCode IN ('0007', '5225', '5225A', '5225B')
    ORDER BY taskCode;
END;

IF OBJECT_ID(N'task.TaskSuggestion', N'U') IS NOT NULL
BEGIN
    SELECT 'task.TaskSuggestion' AS objectName, COUNT_BIG(*) AS rowCount
    FROM task.TaskSuggestion;

    SELECT
        eventId,
        packageId,
        invoiceId,
        runId,
        lineId,
        resultStatus,
        taskCode,
        confidence,
        modelDeployment,
        promptVersion,
        taskMasterSha256,
        createdAtUtc,
        status
    FROM task.TaskSuggestion
    ORDER BY createdAtUtc DESC, eventId, lineId;

    SELECT
        eventId,
        COUNT_BIG(*) AS lineCount,
        SUM(CASE WHEN resultStatus = 'SUGGESTED' THEN 1 ELSE 0 END)
            AS suggestedCount,
        SUM(CASE WHEN resultStatus = 'UNRESOLVED' THEN 1 ELSE 0 END)
            AS unresolvedCount,
        COUNT_BIG(*) - COUNT(DISTINCT lineId) AS duplicateLineCount
    FROM task.TaskSuggestion
    GROUP BY eventId
    ORDER BY eventId;
END;

IF OBJECT_ID(N'capex.AssessmentRun', N'U') IS NOT NULL
BEGIN
    SELECT 'capex.AssessmentRun' AS objectName, COUNT_BIG(*) AS rowCount
    FROM capex.AssessmentRun;

    SELECT
        WorkflowRunId,
        ResultSha256,
        AssessmentCount,
        BundleGroupCount,
        BundleMemberCount,
        CreatedAtUtc,
        InsertedAtUtc
    FROM capex.AssessmentRun
    ORDER BY InsertedAtUtc DESC;
END
ELSE
BEGIN
    SELECT
        'NOT DEPLOYED' AS demoV2AssessmentStore,
        'Migration 007 has not been verified in this database.' AS explanation;
END;

IF OBJECT_ID(N'capex.Assessment', N'U') IS NOT NULL
BEGIN
    SELECT 'capex.Assessment' AS objectName, COUNT_BIG(*) AS rowCount
    FROM capex.Assessment;

    SELECT
        WorkflowRunId,
        Recommendation,
        COUNT_BIG(*) AS rowCount
    FROM capex.Assessment
    GROUP BY WorkflowRunId, Recommendation
    ORDER BY WorkflowRunId, Recommendation;

    SELECT
        WorkflowRunId,
        PackageId,
        InvoiceId,
        LineId,
        SuggestedTaskCode,
        NatureResult,
        UnitCostMeetsThreshold,
        Recommendation,
        BundlingDisposition,
        BundleGroupId,
        ReviewReasons,
        Rationale,
        PolicyVersion
    FROM capex.Assessment
    ORDER BY WorkflowRunId, PackageId, LineId;
END;

IF OBJECT_ID(N'capex.BundleCandidateGroup', N'U') IS NOT NULL
BEGIN
    SELECT
        'capex.BundleCandidateGroup' AS objectName,
        COUNT_BIG(*) AS rowCount
    FROM capex.BundleCandidateGroup;

    SELECT
        WorkflowRunId,
        BundleGroupId,
        SuggestedTaskCode,
        SiteId,
        ProjectId,
        Currency,
        TotalAmount,
        ThresholdAmount,
        Disposition,
        Rationale
    FROM capex.BundleCandidateGroup
    ORDER BY WorkflowRunId, BundleGroupId;
END;

IF OBJECT_ID(N'capex.BundleCandidateMember', N'U') IS NOT NULL
BEGIN
    SELECT
        'capex.BundleCandidateMember' AS objectName,
        COUNT_BIG(*) AS rowCount
    FROM capex.BundleCandidateMember;

    SELECT
        WorkflowRunId,
        BundleGroupId,
        PackageId,
        InvoiceId,
        LineId,
        Quantity,
        UnitCost,
        LineAmount
    FROM capex.BundleCandidateMember
    ORDER BY WorkflowRunId, BundleGroupId, PackageId, LineId;
END;

IF OBJECT_ID(N'pub.v1_Assessment', N'V') IS NOT NULL
BEGIN
    SELECT 'pub.v1_Assessment' AS objectName, COUNT_BIG(*) AS rowCount
    FROM pub.v1_Assessment;
END;
