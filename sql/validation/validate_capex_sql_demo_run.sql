SET NOCOUNT ON;

-- Read-only reconciliation of the November 4 ten-invoice SQL rehearsal.
DECLARE @WorkflowRunId nvarchar(128) = N'friday-sql-20261105T010058Z';
DECLARE @FixtureId nvarchar(128) = N'FRIDAY-DEMO-2026-11-06-V1';

IF (SELECT COUNT_BIG(*) FROM prep.InvoicePackage
    WHERE JSON_VALUE(SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 10
    OR (SELECT COUNT_BIG(*) FROM prep.InvoiceLine AS line
        JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
        WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 30
    THROW 51160, 'Fixture does not contain the verified ten invoices/thirty lines.', 1;

IF (SELECT COUNT_BIG(*) FROM task.TaskSuggestion WHERE correlationId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM pub.v1_Assessment WHERE workflowRunId = @WorkflowRunId) <> 30
    THROW 51161, 'Task, assessment or reporting-view totals do not match the completed run.', 1;

IF EXISTS (
    SELECT 1
    FROM prep.InvoiceLine AS line
    JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
    WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId
      AND (
        (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
            WHERE task.correlationId = @WorkflowRunId
              AND task.packageId = package.PackageId
              AND task.invoiceId = package.InvoiceId
              AND task.lineId = line.InvoiceLineId) <> 1
        OR (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
            WHERE assessment.WorkflowRunId = @WorkflowRunId
              AND assessment.PackageId = package.PackageId
              AND assessment.InvoiceId = package.InvoiceId
              AND assessment.LineId = line.InvoiceLineId) <> 1
      )
)
    THROW 51162, 'A source line is missing, duplicated or associated with the wrong invoice.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    WHERE assessment.WorkflowRunId = @WorkflowRunId
      AND NOT EXISTS (
        SELECT 1 FROM task.TaskSuggestion AS task
        WHERE task.correlationId = assessment.WorkflowRunId
          AND task.packageId = assessment.PackageId
          AND task.invoiceId = assessment.InvoiceId
          AND task.lineId = assessment.LineId
          AND (task.taskCode = assessment.SuggestedTaskCode
            OR (task.taskCode IS NULL AND assessment.SuggestedTaskCode IS NULL))
      )
)
    THROW 51163, 'Task and accounting line identities or task codes do not reconcile.', 1;

IF NOT EXISTS (
    SELECT 1 FROM capex.AssessmentRun
    WHERE WorkflowRunId = @WorkflowRunId
      AND AssessmentCount = 30 AND BundleGroupCount = 1 AND BundleMemberCount = 11
)
    OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId) <> 1
    OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember WHERE WorkflowRunId = @WorkflowRunId) <> 11
    THROW 51164, 'Committed run header and bundle row counts do not match the result evidence.', 1;

IF (SELECT COUNT_BIG(*) FROM capex.Assessment
    WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'CAPEX') <> 14
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'OPEX') <> 14
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'REVIEW') <> 2
    OR EXISTS (SELECT 1 FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND (UsedLlm <> 0 OR PolicyVersion <> @FixtureId OR PolicyVersion IS NULL))
    THROW 51165, 'Accounting outcomes or synthetic policy provenance differ from the actual run.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleCandidateGroup AS bundle
    WHERE bundle.WorkflowRunId = @WorkflowRunId
      AND (
        bundle.Currency <> 'USD' OR bundle.TotalAmount <> 911.08
        OR bundle.ThresholdAmount IS NULL OR bundle.ThresholdAmount <> 1000
        OR bundle.Disposition <> 'CANDIDATE'
        OR bundle.TotalAmount <> (
            SELECT SUM(member.LineAmount) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = bundle.WorkflowRunId
              AND member.BundleGroupId = bundle.BundleGroupId
        )
      )
)
    THROW 51166, 'Bundle amount, currency, threshold or candidate disposition does not reconcile.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleCandidateMember AS member
    JOIN capex.BundleCandidateGroup AS bundle
      ON bundle.WorkflowRunId = member.WorkflowRunId AND bundle.BundleGroupId = member.BundleGroupId
    LEFT JOIN capex.Assessment AS assessment
      ON assessment.WorkflowRunId = member.WorkflowRunId
      AND assessment.PackageId = member.PackageId
      AND assessment.InvoiceId = member.InvoiceId
      AND assessment.LineId = member.LineId
    WHERE member.WorkflowRunId = @WorkflowRunId
      AND (assessment.AssessmentId IS NULL
        OR assessment.BundlingDisposition <> 'GROUPED'
        OR assessment.BundleGroupId IS NULL OR assessment.BundleGroupId <> member.BundleGroupId
        OR assessment.SuggestedTaskCode IS NULL OR assessment.SuggestedTaskCode <> bundle.SuggestedTaskCode)
)
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE WorkflowRunId = @WorkflowRunId AND BundlingDisposition = 'GROUPED') <> 11
    THROW 51167, 'Bundle membership and grouped assessment evidence do not reconcile.', 1;

SELECT package.InvoiceNumber, package.InvoiceId, package.PackageId,
    (SELECT COUNT_BIG(*) FROM prep.InvoiceLine AS line WHERE line.PackageId = package.PackageId) AS SourceLines,
    (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
        WHERE task.correlationId = @WorkflowRunId AND task.packageId = package.PackageId) AS TaskRows,
    (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
        WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.PackageId = package.PackageId) AS AssessmentRows
FROM prep.InvoicePackage AS package
WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId
ORDER BY package.InvoiceNumber;

SELECT Recommendation, COUNT_BIG(*) AS AssessmentRows
FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId GROUP BY Recommendation;

SELECT BundleGroupId, SuggestedTaskCode, SiteId, ProjectId, Currency,
    TotalAmount, ThresholdAmount, Disposition, Rationale
FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId;

SELECT InvoiceId, LineId, SuggestedTaskCode, MissingInputs, ReviewReasons, Rationale
FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId AND Recommendation = 'REVIEW';

SELECT N'SQL_DEMO_RUN_VERIFIED' AS ValidationResult, @WorkflowRunId AS WorkflowRunId,
    N'UNAPPROVED SYNTHETIC REFERENCE REHEARSAL; ZERO LLM CALLS' AS DemoMode;
