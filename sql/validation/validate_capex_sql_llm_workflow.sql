SET NOCOUNT ON;

-- Read-only verification of --llm-workflow after migrations 012 and 013.
-- Use a fresh completed run, never the zero-call reference or advisory run.
DECLARE @WorkflowRunId nvarchar(128) = N'REPLACE_WITH_NEW_GPT_WORKFLOW_RUN_ID';
DECLARE @FixtureId nvarchar(128) = N'FRIDAY-DEMO-2026-11-06-V1';
DECLARE @Model nvarchar(128) = N'gpt-5.1';

IF @WorkflowRunId = N'REPLACE_WITH_NEW_GPT_WORKFLOW_RUN_ID'
    OR @WorkflowRunId = N'friday-sql-20261105T010058Z'
    THROW 51200, 'Set the fresh completed GPT workflow ID, not the reference baseline.', 1;

IF (SELECT COUNT_BIG(*) FROM prep.InvoicePackage
    WHERE JSON_VALUE(SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 10
    OR (SELECT COUNT_BIG(*) FROM prep.InvoiceLine AS line
        JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
        WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId) <> 30
    THROW 51201, 'Expected ten staged real-source invoices and thirty lines.', 1;

IF (SELECT COUNT_BIG(*) FROM task.TaskSuggestion WHERE correlationId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId) <> 30
    OR (SELECT COUNT_BIG(*) FROM capex.BundleDecision WHERE WorkflowRunId = @WorkflowRunId) <> 30
    THROW 51202, 'Expected thirty task suggestions, assessments and model bundle decisions.', 1;

IF EXISTS (
    SELECT 1 FROM prep.InvoiceLine AS line
    JOIN prep.InvoicePackage AS package ON package.PackageId = line.PackageId
    WHERE JSON_VALUE(package.SourceMetadataJson, '$.demoFixture.fixtureId') = @FixtureId AND (
        (SELECT COUNT_BIG(*) FROM task.TaskSuggestion AS task
         WHERE task.correlationId = @WorkflowRunId AND task.packageId = package.PackageId
           AND task.invoiceId = package.InvoiceId AND task.lineId = line.InvoiceLineId) <> 1
        OR (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
            WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.PackageId = package.PackageId
              AND assessment.InvoiceId = package.InvoiceId AND assessment.LineId = line.InvoiceLineId) <> 1
        OR (SELECT COUNT_BIG(*) FROM capex.BundleDecision AS decision
            WHERE decision.WorkflowRunId = @WorkflowRunId AND decision.PackageId = package.PackageId
              AND decision.InvoiceId = package.InvoiceId AND decision.InvoiceLineId = line.InvoiceLineId) <> 1
    )
)
    THROW 51203, 'A source line is missing, duplicated or attributed to the wrong invoice.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    LEFT JOIN task.TaskSuggestion AS task ON task.taskSuggestionId = assessment.TaskSuggestionID
    WHERE assessment.WorkflowRunId = @WorkflowRunId AND (
        assessment.UsedLlm <> 1 OR assessment.ModelVersion IS NULL OR assessment.ModelVersion <> @Model
        OR assessment.PolicyVersion IS NULL OR assessment.PolicyVersion <> @FixtureId
        OR task.taskSuggestionId IS NULL OR task.correlationId <> assessment.WorkflowRunId
        OR task.packageId <> assessment.PackageId OR task.invoiceId <> assessment.InvoiceId
        OR task.lineId <> assessment.LineId OR task.modelDeployment IS NULL OR task.modelDeployment <> @Model
        OR task.MatchMethod IS NULL OR task.MatchMethod NOT IN ('LANGCHAIN', 'REVIEW')
        OR task.ItemIdentified IS NULL OR LEN(task.ItemIdentified) = 0
        OR task.ContractVersion <> '2.0'
        OR (task.taskCode <> assessment.SuggestedTaskCode)
        OR (task.taskCode IS NULL AND assessment.SuggestedTaskCode IS NOT NULL)
        OR (task.taskCode IS NOT NULL AND assessment.SuggestedTaskCode IS NULL)
    )
)
    THROW 51204, 'GPT authority, task metadata, task/assessment links or task codes are inconsistent.', 1;

IF NOT EXISTS (
    SELECT 1 FROM capex.AssessmentRun WHERE WorkflowRunId = @WorkflowRunId AND AssessmentCount = 30
      AND BundleGroupCount = (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId)
      AND BundleMemberCount = (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember WHERE WorkflowRunId = @WorkflowRunId)
)
    THROW 51205, 'Committed assessment/group/member counts do not reconcile.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleCandidateGroup AS bundle WHERE bundle.WorkflowRunId = @WorkflowRunId AND (
        bundle.TotalAmount <= 0 OR bundle.Disposition <> 'CANDIDATE'
        OR bundle.TotalAmount <> (SELECT SUM(member.LineAmount) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = @WorkflowRunId AND member.BundleGroupId = bundle.BundleGroupId)
        OR (SELECT COUNT_BIG(*) FROM capex.BundleCandidateMember AS member
            WHERE member.WorkflowRunId = @WorkflowRunId AND member.BundleGroupId = bundle.BundleGroupId) < 2
    )
)
    THROW 51206, 'Bundle totals, minimum membership or recommendation-only disposition are invalid.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleDecision AS decision
    JOIN capex.Assessment AS assessment ON assessment.WorkflowRunId = decision.WorkflowRunId
      AND assessment.PackageId = decision.PackageId AND assessment.LineId = decision.InvoiceLineId
    WHERE decision.WorkflowRunId = @WorkflowRunId AND (
        decision.UsedLlm <> 1 OR decision.ModelVersion <> @Model
        OR decision.InvoiceId <> assessment.InvoiceId
        OR (decision.ModelDecision = 'BUNDLE' AND (
            assessment.BundleGroupId IS NULL OR assessment.BundleGroupId <> decision.BundleGroupId
            OR assessment.BundlingDisposition <> 'GROUPED'
            OR NOT EXISTS (SELECT 1 FROM capex.BundleCandidateMember AS member
                WHERE member.WorkflowRunId = @WorkflowRunId AND member.BundleGroupId = decision.BundleGroupId
                  AND member.PackageId = decision.PackageId AND member.LineId = decision.InvoiceLineId)
        ))
        OR (decision.ModelDecision <> 'BUNDLE' AND assessment.BundleGroupId IS NOT NULL)
    )
)
    THROW 51207, 'Model bundle decisions do not match the corresponding assessments and group members.', 1;

IF NOT EXISTS (
    SELECT 1 FROM capex.LlmShowcaseRun AS evidence
    WHERE evidence.WorkflowRunId = @WorkflowRunId AND evidence.Status = 'SUCCEEDED'
      AND evidence.ModelDeployment = @Model AND evidence.AccountingInvoiceCount = 10
      AND evidence.AccountingExplanationCount = 30
      AND evidence.LlmCallsAttempted BETWEEN 21 AND 32
      AND evidence.LlmResponsesReceived = evidence.LlmCallsAttempted
      AND JSON_VALUE(evidence.EvidenceJson, '$.pipelineMode') = 'LLM_DECISIONS'
      AND JSON_VALUE(evidence.EvidenceJson, '$.decisionPromptVersions.accounting') = 'sql-llm-accounting-v4'
      AND JSON_VALUE(evidence.EvidenceJson, '$.accountingMaxAttemptsPerInvoice') = '2'
      AND JSON_VALUE(evidence.EvidenceJson, '$.decisionPromptVersions.bundling') = 'sql-llm-bundling-v2'
      AND JSON_VALUE(evidence.EvidenceJson, '$.bundlingMaxAttempts') = '2'
      AND JSON_VALUE(evidence.EvidenceJson, '$.bundlingEligibilityAuthority') = 'CODE_GATE_ON_MODEL_ACCOUNTING_AND_INPUT_FACTS'
      AND JSON_VALUE(evidence.EvidenceJson, '$.status') = 'SUCCEEDED'
      AND JSON_VALUE(evidence.EvidenceJson, '$.workflowRunId') = @WorkflowRunId
      AND JSON_VALUE(evidence.EvidenceJson, '$.inputSha256') = evidence.InputSha256
      AND JSON_VALUE(evidence.EvidenceJson, '$.workflowResultSha256') = evidence.WorkflowResultSha256
      AND LOWER(CONVERT(varchar(64), HASHBYTES('SHA2_256',
            CONVERT(varchar(max), evidence.EvidenceJson)), 2)) = evidence.EvidenceSha256
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')) = evidence.LlmCallsAttempted
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'TASK_SUGGESTION') = 10
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'ACCOUNTING_DECISION') BETWEEN 10 AND 20
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage', ValidationError nvarchar(max) '$.validationError')
           WHERE Stage = 'ACCOUNTING_DECISION' AND ValidationError IS NULL) = 10
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage') WHERE Stage = 'BUNDLE_DECISION') BETWEEN 1 AND 2
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
           WITH (Stage varchar(32) '$.stage', ValidationError nvarchar(max) '$.validationError')
           WHERE Stage = 'BUNDLE_DECISION' AND ValidationError IS NULL) = 1
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.accountingDecisions')) = 10
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.accountingDecisions') AS package
           CROSS APPLY OPENJSON(package.[value], '$.items')) = 30
      AND (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.bundleDecisions.items')) = 30
      AND NOT EXISTS (
          SELECT 1 FROM OPENJSON(evidence.EvidenceJson, '$.calls')
          WITH (Response nvarchar(max) '$.response', Error nvarchar(max) '$.error',
                Stage varchar(32) '$.stage', ValidationError nvarchar(max) '$.validationError',
                Deployment nvarchar(128) '$.deployment') AS call
          WHERE call.Response IS NULL OR LEN(call.Response) = 0 OR call.Error IS NOT NULL
             OR call.Deployment IS NULL OR call.Deployment <> @Model
             OR call.Stage NOT IN ('TASK_SUGGESTION', 'ACCOUNTING_DECISION', 'BUNDLE_DECISION')
             OR (call.ValidationError IS NOT NULL AND call.Stage = 'TASK_SUGGESTION')
      )
      AND NOT EXISTS (
          SELECT 1 FROM OPENJSON(evidence.EvidenceJson, '$.accountingDecisions') AS package
          WHERE (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
                 WITH (Stage varchar(32) '$.stage', PackageId nvarchar(128) '$.packageId') AS call
                 WHERE call.Stage = 'ACCOUNTING_DECISION' AND call.PackageId = package.[key]) NOT BETWEEN 1 AND 2
             OR (SELECT COUNT_BIG(*) FROM OPENJSON(evidence.EvidenceJson, '$.calls')
                 WITH (Stage varchar(32) '$.stage', PackageId nvarchar(128) '$.packageId',
                       ValidationError nvarchar(max) '$.validationError') AS call
                 WHERE call.Stage = 'ACCOUNTING_DECISION' AND call.PackageId = package.[key]
                   AND call.ValidationError IS NULL) <> 1
      )
)
    THROW 51208, 'Complete actual three-stage GPT call evidence or matching SQL hashes are missing.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment WHERE assessment.WorkflowRunId = @WorkflowRunId AND (
        SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun AS evidence
        CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.accountingDecisions') AS package
        CROSS APPLY OPENJSON(package.[value], '$.items')
            WITH (LineId nvarchar(256) '$.lineId', Recommendation varchar(16) '$.recommendation',
                  Rationale nvarchar(max) '$.rationale') AS item
        WHERE evidence.WorkflowRunId = @WorkflowRunId AND package.[key] = assessment.PackageId
          AND item.LineId = assessment.LineId AND item.Recommendation = assessment.Recommendation
          AND item.Rationale = assessment.Rationale
    ) <> 1
)
    THROW 51209, 'A native assessment differs from its actual model recommendation.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment WHERE assessment.WorkflowRunId = @WorkflowRunId AND (
        SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun AS evidence
        CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.calls')
            WITH (PackageId nvarchar(128) '$.packageId', Stage varchar(32) '$.stage',
                  Response nvarchar(max) '$.response', ValidationError nvarchar(max) '$.validationError') AS call
        CROSS APPLY OPENJSON(call.Response, '$.items')
            WITH (LineId nvarchar(256) '$.lineId', Recommendation varchar(16) '$.recommendation',
                  NatureResult varchar(32) '$.natureResult', Rationale nvarchar(max) '$.rationale') AS item
        WHERE evidence.WorkflowRunId = @WorkflowRunId AND call.PackageId = assessment.PackageId
          AND call.Stage = 'ACCOUNTING_DECISION' AND call.ValidationError IS NULL
          AND item.LineId = assessment.LineId AND item.Recommendation = assessment.Recommendation
          AND item.NatureResult = assessment.NatureResult AND item.Rationale = assessment.Rationale
    ) <> 1
)
    THROW 51212, 'A native assessment does not match exactly one accepted raw model response.', 1;

IF EXISTS (
    SELECT 1 FROM task.TaskSuggestion AS task WHERE task.correlationId = @WorkflowRunId AND NOT EXISTS (
        SELECT 1 FROM capex.LlmShowcaseRun AS evidence
        CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.calls')
            WITH (CallId nvarchar(128) '$.callId', PackageId nvarchar(128) '$.packageId',
                  Stage varchar(32) '$.stage') AS call
        CROSS APPLY OPENJSON(task.evidenceRefs) AS reference
        WHERE evidence.WorkflowRunId = @WorkflowRunId AND call.Stage = 'TASK_SUGGESTION'
          AND call.PackageId = task.packageId AND reference.[value] = call.CallId
    )
)
    THROW 51210, 'Every task must reference its own invoice model call.', 1;

IF EXISTS (
    SELECT 1 FROM capex.BundleDecision AS decision WHERE decision.WorkflowRunId = @WorkflowRunId AND (
        SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun AS evidence
        CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.bundleDecisions.items')
            WITH (PackageId nvarchar(128) '$.packageId', InvoiceId nvarchar(128) '$.invoiceId',
                  LineId nvarchar(256) '$.lineId', Decision varchar(16) '$.decision',
                  ProposalId nvarchar(128) '$.proposalId', Rationale nvarchar(max) '$.rationale') AS item
        OUTER APPLY (
            SELECT mapping.[value] AS BundleGroupId
            FROM OPENJSON(evidence.EvidenceJson, '$.bundleGroupIds') AS mapping
            WHERE mapping.[key] = item.ProposalId
        ) AS mapped
        WHERE evidence.WorkflowRunId = @WorkflowRunId AND item.PackageId = decision.PackageId
          AND item.InvoiceId = decision.InvoiceId AND item.LineId = decision.InvoiceLineId
          AND item.Decision = decision.ModelDecision AND item.Rationale = decision.Rationale
          AND (mapped.BundleGroupId = decision.BundleGroupId
               OR (mapped.BundleGroupId IS NULL AND decision.BundleGroupId IS NULL))
    ) <> 1
)
    THROW 51211, 'A native bundle decision differs from its actual model recommendation.', 1;

SELECT N'GPT_INVOICE_WORKFLOW_VERIFIED' AS ValidationResult, @WorkflowRunId AS WorkflowRunId,
    N'GPT-5.1 RECOMMENDATIONS; UNAPPROVED DEMO REFERENCES; NO HUMAN APPROVAL OR ERP POSTING' AS DemoMode;

SELECT task.invoiceId, task.InvoiceLineID, task.ItemIdentified, task.SuggestedTaskCode,
    task.SuggestedAssetClass, task.MatchMethod, task.ModelVersion, task.rationale
FROM task.TaskSuggestion AS task WHERE task.correlationId = @WorkflowRunId
ORDER BY task.packageId, task.lineId;

SELECT assessment.InvoiceId, assessment.InvoiceLineID, assessment.TaskSuggestionID,
    assessment.CapExOpExSuggestion, assessment.NatureResult, assessment.Rationale,
    assessment.UsedLlm, assessment.ModelVersion, assessment.BundlingDisposition, assessment.BundleGroupId
FROM capex.Assessment AS assessment WHERE assessment.WorkflowRunId = @WorkflowRunId
ORDER BY assessment.PackageId, assessment.LineId;

SELECT decision.InvoiceId, decision.InvoiceLineId, decision.ModelDecision,
    decision.BundleGroupId, decision.IsOneAssetSuggestion, decision.Confidence, decision.Rationale
FROM capex.BundleDecision AS decision WHERE decision.WorkflowRunId = @WorkflowRunId
ORDER BY decision.PackageId, decision.InvoiceLineId;

SELECT bundle.BundleGroupId, bundle.SuggestedTaskCode, bundle.SiteId, bundle.ProjectId,
    bundle.Currency, bundle.TotalAmount, bundle.ThresholdAmount, bundle.Rationale,
    COUNT_BIG(member.LineId) AS MemberCount, COUNT(DISTINCT member.InvoiceId) AS DistinctInvoiceCount
FROM capex.BundleCandidateGroup AS bundle
JOIN capex.BundleCandidateMember AS member ON member.WorkflowRunId = bundle.WorkflowRunId
    AND member.BundleGroupId = bundle.BundleGroupId
WHERE bundle.WorkflowRunId = @WorkflowRunId
GROUP BY bundle.BundleGroupId, bundle.SuggestedTaskCode, bundle.SiteId, bundle.ProjectId,
    bundle.Currency, bundle.TotalAmount, bundle.ThresholdAmount, bundle.Rationale;
