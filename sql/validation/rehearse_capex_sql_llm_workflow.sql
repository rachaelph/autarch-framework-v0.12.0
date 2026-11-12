SET NOCOUNT ON;

-- Frozen Friday run. Read-only: no new model calls or workflow execution.
DECLARE @WorkflowRunId nvarchar(128) = N'friday-sql-gpt-contract-v4-20261105T150000Z';
DECLARE @DemoInvoiceId nvarchar(128) = N'58aceec5-f35a-48a7-8faa-2f1a195af203';

IF NOT EXISTS (
    SELECT 1 FROM capex.LlmShowcaseRun
    WHERE WorkflowRunId = @WorkflowRunId AND Status = 'SUCCEEDED'
)
    THROW 51220, 'The frozen demo run must have a complete successful SQL model audit.', 1;

SELECT N'1. Ten invoices, thirty lines' AS DemoStep,
    invoice.VendorName, invoice.InvoiceNumber, assessment.InvoiceId,
    COUNT_BIG(*) AS LineCount,
    SUM(CASE WHEN assessment.Recommendation = 'CAPEX' THEN 1 ELSE 0 END) AS CapExCount,
    SUM(CASE WHEN assessment.Recommendation = 'OPEX' THEN 1 ELSE 0 END) AS OpExCount,
    SUM(CASE WHEN assessment.Recommendation = 'REVIEW' THEN 1 ELSE 0 END) AS ReviewCount
FROM capex.Assessment AS assessment
JOIN prep.Invoice AS invoice ON invoice.InvoiceID = assessment.InvoiceId
WHERE assessment.WorkflowRunId = @WorkflowRunId
GROUP BY invoice.VendorName, invoice.InvoiceNumber, assessment.InvoiceId
ORDER BY invoice.VendorName, invoice.InvoiceNumber;

SELECT N'2. GPT task suggestions: Kimley-Horn' AS DemoStep,
    invoice.InvoiceNumber, line.Description AS SourceDescription,
    task.InvoiceLineID, task.ItemIdentified, task.SuggestedTaskCode,
    task.SuggestedAssetClass, task.MatchMethod, task.ModelVersion,
    task.rationale AS ModelTaskRationale
FROM task.TaskSuggestion AS task
JOIN prep.Invoice AS invoice ON invoice.InvoiceID = task.invoiceId
JOIN prep.InvoiceLine AS line ON line.PackageId = task.packageId
    AND line.InvoiceLineId = task.lineId
WHERE task.correlationId = @WorkflowRunId AND task.invoiceId = @DemoInvoiceId
ORDER BY line.LineNumber;

SELECT N'3. GPT accounting linked to the task and source line' AS DemoStep,
    invoice.InvoiceNumber, line.Description AS SourceDescription,
    assessment.InvoiceLineID, assessment.TaskSuggestionID,
    assessment.SuggestedTaskCode, assessment.NatureResult,
    assessment.CapExOpExSuggestion, assessment.ThresholdAmount,
    assessment.ThresholdCurrency, assessment.Rationale AS ModelAccountingRationale,
    assessment.UsedLlm, assessment.ModelVersion
FROM capex.Assessment AS assessment
JOIN prep.Invoice AS invoice ON invoice.InvoiceID = assessment.InvoiceId
JOIN prep.InvoiceLine AS line ON line.PackageId = assessment.PackageId
    AND line.InvoiceLineId = assessment.LineId
WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.InvoiceId = @DemoInvoiceId
ORDER BY line.LineNumber;

SELECT N'4. GPT bundling considered all invoices' AS DemoStep,
    invoice.VendorName, invoice.InvoiceNumber, line.Description AS SourceDescription,
    decision.InvoiceLineId, decision.ModelDecision, decision.BundleGroupId,
    decision.Confidence, decision.Rationale AS ModelBundleRationale
FROM capex.BundleDecision AS decision
JOIN prep.Invoice AS invoice ON invoice.InvoiceID = decision.InvoiceId
JOIN prep.InvoiceLine AS line ON line.PackageId = decision.PackageId
    AND line.InvoiceLineId = decision.InvoiceLineId
WHERE decision.WorkflowRunId = @WorkflowRunId
ORDER BY invoice.VendorName, invoice.InvoiceNumber, line.LineNumber;

SELECT N'5. Audit and persisted output counts' AS DemoStep,
    evidence.WorkflowRunId, evidence.Status, evidence.ModelDeployment,
    evidence.LlmCallsAttempted, evidence.LlmResponsesReceived,
    (SELECT COUNT_BIG(*) FROM task.TaskSuggestion
     WHERE correlationId = @WorkflowRunId) AS TaskSuggestionCount,
    (SELECT COUNT_BIG(*) FROM capex.Assessment
     WHERE WorkflowRunId = @WorkflowRunId) AS AssessmentCount,
    (SELECT COUNT_BIG(*) FROM capex.BundleDecision
     WHERE WorkflowRunId = @WorkflowRunId) AS ModelBundleDecisionCount,
    (SELECT COUNT_BIG(*) FROM capex.BundleCandidateGroup
     WHERE WorkflowRunId = @WorkflowRunId) AS ProposedBundleGroupCount,
    N'Unapproved synthetic demo context; recommendations, not human approvals or ERP postings.' AS DemoCaveat
FROM capex.LlmShowcaseRun AS evidence
WHERE evidence.WorkflowRunId = @WorkflowRunId;
