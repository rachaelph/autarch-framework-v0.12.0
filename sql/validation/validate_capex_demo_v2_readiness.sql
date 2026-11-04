SET NOCOUNT ON;

-- Set these to the completed run and its explicit demo mode before execution.
DECLARE @WorkflowRunId nvarchar(128) = N'REPLACE_WITH_COMPLETED_WORKFLOW_RUN_ID';
DECLARE @Synthetic bit = 1;

IF @WorkflowRunId = N'REPLACE_WITH_COMPLETED_WORKFLOW_RUN_ID'
    OR LEN(LTRIM(RTRIM(@WorkflowRunId))) = 0
    THROW 51082, 'Set @WorkflowRunId to the completed workflow ID. Run once for each demo mode.', 1;

IF OBJECT_ID(N'task.TaskSuggestion', N'U') IS NULL
    OR OBJECT_ID(N'pub.v1_TaskMaster', N'V') IS NULL
    OR OBJECT_ID(N'capex.AssessmentRun', N'U') IS NULL
    OR OBJECT_ID(N'capex.Assessment', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateGroup', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateMember', N'U') IS NULL
    OR OBJECT_ID(N'pub.v1_Assessment', N'V') IS NULL
    THROW 51083, 'Demo V2 schema is incomplete. Run the inventory and apply missing migrations.', 1;

DECLARE @Expected TABLE
(
    InvoiceName nvarchar(64) NOT NULL,
    PackageId nvarchar(128) NOT NULL PRIMARY KEY,
    InvoiceId nvarchar(128) NOT NULL,
    ExpectedLines int NOT NULL
);

INSERT INTO @Expected VALUES
    (N'Bruegmann', N'pkg-56a5c9fc-5b7c-5cef-9828-65c320c18048',
        N'135d03ac-3598-498a-83fe-d273d046bfea', 12),
    (N'Kimley-Horn', N'pkg-ebdd3ca9-ed08-519c-b2eb-28d6627fb6be',
        N'58aceec5-f35a-48a7-8faa-2f1a195af203', 10),
    (N'JC2000A07814 / invoice 20294', N'pkg-8a841383-3ec4-5173-a24d-3c506eb63d24',
        N'JC2000A07814:20294', 1);

SELECT
    expected.InvoiceName,
    expected.ExpectedLines,
    (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
        WHERE task.correlationId = @WorkflowRunId
          AND task.packageId = expected.PackageId
          AND task.invoiceId = expected.InvoiceId) AS TaskRows,
    (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
        WHERE assessment.WorkflowRunId = @WorkflowRunId
          AND assessment.PackageId = expected.PackageId
          AND assessment.InvoiceId = expected.InvoiceId) AS AssessmentRows
FROM @Expected AS expected
ORDER BY expected.InvoiceName;

IF NOT EXISTS
(
    SELECT 1 FROM capex.AssessmentRun
    WHERE WorkflowRunId = @WorkflowRunId
      AND AssessmentCount = 23
      AND BundleGroupCount = CASE WHEN @Synthetic = 1 THEN 1 ELSE 0 END
      AND BundleMemberCount = CASE WHEN @Synthetic = 1 THEN 9 ELSE 0 END
)
    THROW 51084, 'Completed run header is missing or does not match the corrected three-invoice baseline.', 1;

IF EXISTS
(
    SELECT 1 FROM @Expected AS expected
    WHERE (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
        WHERE task.correlationId = @WorkflowRunId
          AND task.packageId = expected.PackageId
          AND task.invoiceId = expected.InvoiceId) <> expected.ExpectedLines
       OR (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
        WHERE assessment.WorkflowRunId = @WorkflowRunId
          AND assessment.PackageId = expected.PackageId
          AND assessment.InvoiceId = expected.InvoiceId) <> expected.ExpectedLines
)
    THROW 51085, 'At least one invoice is missing task or assessment rows, or has duplicates.', 1;

IF (SELECT COUNT_BIG(*) FROM task.TaskSuggestion
    WHERE correlationId = @WorkflowRunId) <> 23
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId) <> 23
    OR (SELECT COUNT_BIG(*) FROM pub.v1_Assessment
        WHERE workflowRunId = @WorkflowRunId) <> 23
    THROW 51086, 'Run totals contain missing or unexpected lines, or the reporting view does not reconcile.', 1;

IF EXISTS
(
    SELECT 1 FROM task.TaskSuggestion
    WHERE correlationId = @WorkflowRunId
    GROUP BY packageId, invoiceId, lineId
    HAVING COUNT_BIG(*) <> 1
)
    THROW 51087, 'Duplicate task determinations were found for a workflow line.', 1;

IF EXISTS
(
    SELECT 1 FROM capex.Assessment AS assessment
    WHERE assessment.WorkflowRunId = @WorkflowRunId
      AND NOT EXISTS
      (
          SELECT 1 FROM task.TaskSuggestion AS task
          WHERE task.correlationId = assessment.WorkflowRunId
            AND task.packageId = assessment.PackageId
            AND task.invoiceId = assessment.InvoiceId
            AND task.lineId = assessment.LineId
            AND (task.taskCode = assessment.SuggestedTaskCode
                OR (task.taskCode IS NULL AND assessment.SuggestedTaskCode IS NULL))
      )
)
    THROW 51088, 'Task determinations do not match assessment line identities and task codes.', 1;

IF EXISTS
(
    SELECT 1 FROM task.TaskSuggestion AS task
    WHERE task.correlationId = @WorkflowRunId
      AND task.taskCode IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM pub.v1_TaskMaster AS master
          WHERE master.regionCode = 'US' AND master.taskCode = task.taskCode
      )
)
    THROW 51089, 'A suggested task is absent from the current governed US TaskMaster.', 1;

IF (SELECT COUNT_BIG(*) FROM capex.Assessment
    WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'CAPEX')
        <> CASE WHEN @Synthetic = 1 THEN 7 ELSE 0 END
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'OPEX')
        <> CASE WHEN @Synthetic = 1 THEN 11 ELSE 0 END
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'REVIEW')
        <> CASE WHEN @Synthetic = 1 THEN 5 ELSE 23 END
    THROW 51090, 'Accounting outcomes differ from the selected demo baseline; inspect the result artifact and assumptions.', 1;

IF (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup
    WHERE WorkflowRunId = @WorkflowRunId)
        <> CASE WHEN @Synthetic = 1 THEN 1 ELSE 0 END
    OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember
        WHERE WorkflowRunId = @WorkflowRunId)
        <> CASE WHEN @Synthetic = 1 THEN 9 ELSE 0 END
    THROW 51091, 'Bundle group or member totals do not reconcile.', 1;

IF EXISTS
(
    SELECT 1 FROM capex.BundleCandidateGroup AS bundle
    WHERE bundle.WorkflowRunId = @WorkflowRunId
      AND (bundle.TotalAmount <= 0
        OR bundle.TotalAmount <> (
            SELECT SUM(member.LineAmount) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = bundle.WorkflowRunId
              AND member.BundleGroupId = bundle.BundleGroupId)
        OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = bundle.WorkflowRunId
              AND member.BundleGroupId = bundle.BundleGroupId) < 2)
)
    THROW 51092, 'A bundle has no positive value, too few members, or an incorrect total.', 1;

IF EXISTS
(
    SELECT 1 FROM capex.Assessment AS assessment
    WHERE assessment.WorkflowRunId = @WorkflowRunId
      AND (
        (assessment.BundlingDisposition = 'GROUPED' AND NOT EXISTS (
            SELECT 1 FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = assessment.WorkflowRunId
              AND member.BundleGroupId = assessment.BundleGroupId
              AND member.PackageId = assessment.PackageId
              AND member.InvoiceId = assessment.InvoiceId
              AND member.LineId = assessment.LineId))
        OR (assessment.BundlingDisposition <> 'GROUPED' AND EXISTS (
            SELECT 1 FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = assessment.WorkflowRunId
              AND member.PackageId = assessment.PackageId
              AND member.LineId = assessment.LineId)))
)
    THROW 51093, 'Bundle membership and grouped assessments do not match.', 1;

SELECT
    'PASS' AS ReadinessStatus,
    DB_NAME() AS DatabaseName,
    @WorkflowRunId AS WorkflowRunId,
    CASE WHEN @Synthetic = 1
        THEN 'UNAPPROVED SYNTHETIC DEMO - NOT CIRCLE K ACCOUNTING POLICY'
        ELSE 'SOURCE DATA WITH MISSING POLICY - REVIEW ONLY'
    END AS DemoMode,
    SYSUTCDATETIME() AS CheckedAtUtc;

SELECT Recommendation, COUNT_BIG(*) AS LineCount
FROM capex.Assessment
WHERE WorkflowRunId = @WorkflowRunId
GROUP BY Recommendation;

SELECT BundleGroupId, SuggestedTaskCode, SiteId, ProjectId, Currency, TotalAmount
FROM capex.BundleCandidateGroup
WHERE WorkflowRunId = @WorkflowRunId;
