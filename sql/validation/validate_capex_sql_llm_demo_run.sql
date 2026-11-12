SET NOCOUNT ON;

-- Read-only verification of a NEW model-assisted run, not the zero-call baseline.
-- Apply migration 012 first. Set the new run ID, and verify the completed job
-- marker plus the matching SQL/Blob output hashes. Raw responses are selected below.
DECLARE @WorkflowRunId nvarchar(128) = N'REPLACE_WITH_NEW_LLM_WORKFLOW_RUN_ID';
DECLARE @FixtureId nvarchar(128) = N'FRIDAY-DEMO-2026-11-06-V1';
DECLARE @ModelDeployment nvarchar(128) = N'gpt-5.1';

IF @WorkflowRunId = N'REPLACE_WITH_NEW_LLM_WORKFLOW_RUN_ID'
    OR @WorkflowRunId = N'friday-sql-20261105T010058Z'
    THROW 51180, 'Select the NEW model-assisted run ID; do not reuse the reference baseline.', 1;

IF (SELECT COUNT_BIG(*) FROM prep.InvoicePackage
    WHERE JSON_VALUE(SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 10
    OR (SELECT COUNT_BIG(*) FROM prep.InvoiceLine AS line
        JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
        WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 30
    THROW 51181, 'Expected ten source invoices and thirty lines.', 1;

IF (SELECT COUNT_BIG(*) FROM task.TaskSuggestion WHERE correlationId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM pub.v1_Assessment WHERE workflowRunId = @WorkflowRunId) <> 30
    THROW 51182, 'Task, assessment and reporting totals do not match expected coverage.', 1;

IF EXISTS (
    SELECT 1 FROM prep.InvoiceLine AS line
    JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
    WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId
      AND (
        (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
         WHERE task.correlationId = @WorkflowRunId AND task.packageId = package.PackageId
           AND task.invoiceId = package.InvoiceId AND task.lineId = line.InvoiceLineId) <> 1
        OR (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
            WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.PackageId = package.PackageId
              AND assessment.InvoiceId = package.InvoiceId AND assessment.LineId = line.InvoiceLineId) <> 1
      )
)
    THROW 51183, 'A source line is missing, duplicated or attributed to the wrong invoice.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    WHERE assessment.WorkflowRunId = @WorkflowRunId AND (
        assessment.UsedLlm <> 0 OR assessment.PolicyVersion <> @FixtureId
        OR assessment.PolicyVersion IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM task.TaskSuggestion AS task
            WHERE task.correlationId = @WorkflowRunId AND task.packageId = assessment.PackageId
              AND task.invoiceId = assessment.InvoiceId AND task.lineId = assessment.LineId
              AND (task.taskCode = assessment.SuggestedTaskCode
                   OR (task.taskCode IS NULL AND assessment.SuggestedTaskCode IS NULL))
        )
    )
)
    THROW 51184, 'Accounting authority, provenance or task agreement is invalid.', 1;

IF EXISTS (SELECT 1 FROM task.TaskSuggestion
    WHERE correlationId = @WorkflowRunId
      AND (modelDeployment <> @ModelDeployment OR modelDeployment IS NULL))
    OR NOT EXISTS (
        SELECT 1 FROM task.TaskSuggestion AS task
        CROSS APPLY OPENJSON(task.evidenceRefs) AS evidence
        WHERE task.correlationId = @WorkflowRunId AND evidence.[value] LIKE N'llm-call:%'
    )
    THROW 51185, 'Task model deployment or captured-call references are missing.', 1;

IF NOT EXISTS (
    SELECT 1 FROM capex.AssessmentRun
    WHERE WorkflowRunId = @WorkflowRunId AND AssessmentCount = 30
      AND BundleGroupCount = (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup
                              WHERE WorkflowRunId = @WorkflowRunId)
      AND BundleMemberCount = (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember
                               WHERE WorkflowRunId = @WorkflowRunId)
)
    THROW 51186, 'Committed bundle counts do not match the run header.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleCandidateGroup AS bundle
    WHERE bundle.WorkflowRunId = @WorkflowRunId AND (
        bundle.TotalAmount <> (SELECT SUM(member.LineAmount) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = @WorkflowRunId AND member.BundleGroupId = bundle.BundleGroupId)
        OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = @WorkflowRunId AND member.BundleGroupId = bundle.BundleGroupId) < 2
    )
)
    THROW 51187, 'Bundle amounts or minimum membership do not reconcile.', 1;

IF NOT EXISTS (
    SELECT 1 FROM capex.LlmShowcaseRun AS evidence
    WHERE evidence.WorkflowRunId = @WorkflowRunId AND evidence.Status = 'SUCCEEDED'
      AND evidence.ModelDeployment = @ModelDeployment
      AND evidence.AccountingInvoiceCount = 10 AND evidence.AccountingExplanationCount = 30
      AND evidence.BundleExplanationCount = (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup
                                             WHERE WorkflowRunId = @WorkflowRunId)
      AND evidence.LlmCallsAttempted = evidence.LlmResponsesReceived AND evidence.LlmCallsAttempted > 0
      AND JSON_VALUE(evidence.EvidenceJson, '$.workflowRunId') = @WorkflowRunId
      AND JSON_VALUE(evidence.EvidenceJson, '$.status') = 'SUCCEEDED'
      AND JSON_VALUE(evidence.EvidenceJson, '$.inputSha256') = evidence.InputSha256
      AND JSON_VALUE(evidence.EvidenceJson, '$.workflowResultSha256') = evidence.WorkflowResultSha256
      AND LOWER(CONVERT(varchar(64), HASHBYTES('SHA2_256',
            CONVERT(varchar(max), evidence.EvidenceJson)), 2)) = evidence.EvidenceSha256
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')) = evidence.LlmCallsAttempted
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.accountingExplanations')) = 10
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.accountingExplanations') AS package
           CROSS APPLY OPENJSON(package.[value], '$.items')) = 30
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.bundleExplanation.items')) = evidence.BundleExplanationCount
      AND EXISTS (
          SELECT 1 FROM OPENJSON(evidence.EvidenceJson, '$.bundleExplanation')
          WITH (Summary nvarchar(max) '$.summary', Authority varchar(32) '$.authority',
                PolicyApproval varchar(32) '$.policyApproval') AS explanation
          WHERE LEN(explanation.Summary) > 0 AND explanation.Authority = 'ADVISORY_UNREVIEWED'
            AND explanation.PolicyApproval = 'UNAPPROVED_SYNTHETIC_DEMO'
      )
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'ACCOUNTING_EXPLANATION') = 10
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'BUNDLE_EXPLANATION') = 1
      AND EXISTS (SELECT 1 FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'TASK_SUGGESTION')
      AND NOT EXISTS (
          SELECT 1 FROM OPENJSON(evidence.EvidenceJson, '$.calls')
          WITH (Response nvarchar(max) '$.response', Error nvarchar(max) '$.error',
                Deployment nvarchar(128) '$.deployment') AS call
          WHERE call.Response IS NULL OR LEN(call.Response) = 0
             OR call.Error IS NOT NULL OR call.Deployment <> @ModelDeployment OR call.Deployment IS NULL
      )
)
    THROW 51188, 'Actual LLM outputs, hashes or completed explanation counts were not persisted correctly in SQL.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    WHERE assessment.WorkflowRunId = @WorkflowRunId AND (
        SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun AS evidence
        CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.accountingExplanations') AS package
        CROSS APPLY OPENJSON(package.[value], '$.items')
        WITH (ReferenceId nvarchar(512) '$.referenceId', Outcome varchar(16) '$.outcome') AS explanation
        WHERE evidence.WorkflowRunId = @WorkflowRunId AND package.[key] = assessment.PackageId
          AND explanation.ReferenceId = CONCAT(N'assessment:', assessment.PackageId, N':', assessment.LineId)
          AND explanation.Outcome = assessment.Recommendation
    ) <> 1
)
    OR EXISTS (
        SELECT 1 FROM capex.BundleCandidateGroup AS bundle
        WHERE bundle.WorkflowRunId = @WorkflowRunId AND (
            SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun AS evidence
            CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.bundleExplanation.items')
            WITH (ReferenceId nvarchar(512) '$.referenceId', Outcome varchar(16) '$.outcome') AS explanation
            WHERE evidence.WorkflowRunId = @WorkflowRunId
              AND explanation.ReferenceId = CONCAT(N'bundle:', bundle.BundleGroupId)
              AND explanation.Outcome = bundle.Disposition
        ) <> 1
    )
    THROW 51189, 'SQL LLM explanations do not match the authoritative assessment and bundle outcomes.', 1;

SELECT package.InvoiceId, COUNT_BIG(*) AS Lines,
       SUM(CASE WHEN assessment.Recommendation = 'CAPEX' THEN 1 ELSE 0 END) AS CapEx,
       SUM(CASE WHEN assessment.Recommendation = 'OPEX' THEN 1 ELSE 0 END) AS OpEx,
       SUM(CASE WHEN assessment.Recommendation = 'REVIEW' THEN 1 ELSE 0 END) AS Review
FROM prep.InvoicePackage AS package
JOIN capex.Assessment AS assessment ON assessment.PackageId = package.PackageId
WHERE assessment.WorkflowRunId = @WorkflowRunId
GROUP BY package.InvoiceId;

SELECT task.invoiceId, task.lineId, line.Description, task.taskCode,
       task.confidence, task.rationale, task.evidenceRefs,
       CASE WHEN EXISTS (
           SELECT 1 FROM OPENJSON(task.evidenceRefs) AS evidence
           WHERE evidence.[value] LIKE N'llm-call:%'
       ) THEN N'MODEL_ASSISTED' ELSE N'DETERMINISTIC_SOURCE_OR_REVIEW' END AS TaskExecution
FROM task.TaskSuggestion AS task
JOIN prep.InvoiceLine AS line ON line.PackageId = task.packageId AND line.InvoiceLineId = task.lineId
WHERE task.correlationId = @WorkflowRunId
ORDER BY task.invoiceId, task.lineId;

SELECT BundleGroupId, SuggestedTaskCode, Currency, TotalAmount, ThresholdAmount, Disposition
FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId;

SELECT call.*
FROM capex.LlmShowcaseRun AS evidence
CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.calls') WITH (
    CallId nvarchar(128) '$.callId', Stage varchar(32) '$.stage',
    PackageId nvarchar(128) '$.packageId', Deployment nvarchar(128) '$.deployment',
    SystemPrompt nvarchar(max) '$.systemPrompt', Prompt nvarchar(max) '$.prompt',
    Response nvarchar(max) '$.response', Error nvarchar(max) '$.error'
) AS call
WHERE evidence.WorkflowRunId = @WorkflowRunId;

SELECT package.[key] AS PackageId, explanation.*
FROM capex.LlmShowcaseRun AS evidence
CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.accountingExplanations') AS package
CROSS APPLY OPENJSON(package.[value], '$.items') WITH (
    ReferenceId nvarchar(512) '$.referenceId', Outcome varchar(16) '$.outcome',
    Explanation nvarchar(max) '$.explanation', EvidenceRefs nvarchar(max) '$.evidenceRefs' AS JSON
) AS explanation
WHERE evidence.WorkflowRunId = @WorkflowRunId;

SELECT explanation.Summary AS BundleSummary, explanation.Items AS BundleExplanations,
       evidence.EvidenceJson AS CompleteLlmOutputJson
FROM capex.LlmShowcaseRun AS evidence
CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.bundleExplanation')
WITH (Summary nvarchar(max) '$.summary', Items nvarchar(max) '$.items' AS JSON) AS explanation
WHERE evidence.WorkflowRunId = @WorkflowRunId;

SELECT N'SQL_LLM_RESULTS_VERIFIED' AS ValidationResult, @WorkflowRunId AS WorkflowRunId,
       N'UNAPPROVED SYNTHETIC REFERENCES; ACTUAL LLM OUTPUTS STORED IN SQL' AS DemoMode;
