SET NOCOUNT ON;

-- Read-only presenter views. Change only this declaration to a completed ADF RunId.
DECLARE @WorkflowRunId nvarchar(128) = N'REPLACE_WITH_ADF_RUN_ID';
IF @WorkflowRunId = N'REPLACE_WITH_ADF_RUN_ID'
    THROW 51230, 'Set the completed ADF RunId before presenting results.', 1;

DECLARE @Payload nvarchar(max) = (
    SELECT payload FROM mvp_review.imports WHERE run_id = @WorkflowRunId
);
IF @Payload IS NULL OR ISJSON(@Payload) <> 1
    THROW 51231, 'No committed result envelope exists for this ADF RunId.', 1;

SELECT @WorkflowRunId AS WorkflowRunId,
    JSON_VALUE(@Payload, '$.businessApproved') AS BusinessApproved,
    JSON_VALUE(@Payload, '$.reviewStatus') AS ReviewStatus,
    COUNT_BIG(*) AS AssessmentLines,
    SUM(CASE WHEN UsedLlm = 1 THEN 1 ELSE 0 END) AS ModelBackedLines,
    SUM(CASE WHEN Recommendation = 'CAPEX' THEN 1 ELSE 0 END) AS CapEx,
    SUM(CASE WHEN Recommendation = 'OPEX' THEN 1 ELSE 0 END) AS OpEx,
    SUM(CASE WHEN Recommendation = 'REVIEW' THEN 1 ELSE 0 END) AS ReviewRequired,
    SUM(CASE WHEN NatureResult = 'CAPITALIZABLE' THEN 1 ELSE 0 END) AS CapitalizableNatureLines,
    (SELECT COUNT_BIG(*) FROM task.TaskSuggestion
        WHERE correlationId = @WorkflowRunId AND taskCode IS NOT NULL) AS ResolvedTaskSuggestions,
    (SELECT COUNT_BIG(*) FROM task.assetClassSuggestion
        WHERE correlationId = @WorkflowRunId AND assetClassCode IS NOT NULL) AS ResolvedAssetClassSuggestions
FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId;

SELECT * FROM capex.AssessmentRun WHERE WorkflowRunId = @WorkflowRunId;

SELECT assessment.InvoiceId, COUNT_BIG(*) AS InvoiceLines,
    SUM(CASE WHEN task.taskCode IS NOT NULL THEN 1 ELSE 0 END) AS SelectedTaskCodes,
    SUM(CASE WHEN asset.assetClassCode IS NOT NULL THEN 1 ELSE 0 END) AS SelectedAssetClassCodes,
    SUM(CASE WHEN assessment.Recommendation = 'CAPEX' THEN 1 ELSE 0 END) AS CapEx,
    SUM(CASE WHEN assessment.Recommendation = 'OPEX' THEN 1 ELSE 0 END) AS OpEx,
    SUM(CASE WHEN assessment.Recommendation = 'REVIEW' THEN 1 ELSE 0 END) AS AccountingReview,
    (SELECT COUNT_BIG(*) FROM capex.BundleDecision AS decision
        WHERE decision.WorkflowRunId = @WorkflowRunId
          AND decision.InvoiceId = assessment.InvoiceId) AS ModelBundleDecisions
FROM capex.Assessment AS assessment
LEFT JOIN task.TaskSuggestion AS task
    ON task.taskSuggestionId = assessment.TaskSuggestionID
    AND task.correlationId = assessment.WorkflowRunId
LEFT JOIN task.assetClassSuggestion AS asset
    ON asset.assetClassSuggestionId = assessment.AssetClassSuggestionID
    AND asset.correlationId = assessment.WorkflowRunId
WHERE assessment.WorkflowRunId = @WorkflowRunId
GROUP BY assessment.InvoiceId
ORDER BY assessment.InvoiceId;

SELECT file_name AS SourceFile, source_format AS SourceFormat,
    source_sha256 AS SourceSha256, package_ids AS InvoicePackageIds
FROM OPENJSON(@Payload, '$.sourceManifest.sources')
WITH (
    file_name nvarchar(512) '$.fileName', source_format nvarchar(64) '$.format',
    source_sha256 char(64) '$.sha256', package_ids nvarchar(max) '$.packageIds' AS JSON
);

SELECT document_id AS InvoiceId, invoice_date AS ExtractedInvoiceDate,
    eligibility_mode AS RecommendationMode, review_reasons AS SourceWarnings
FROM OPENJSON(@Payload, '$.documents')
WITH (
    document_id nvarchar(128) '$.document.documentId',
    invoice_date nvarchar(64) '$.extraction.invoiceDate',
    eligibility_mode nvarchar(64) '$.extraction.recommendationEligibility.mode',
    review_reasons nvarchar(max) '$.reviewReasons' AS JSON
);

SELECT assessment.PackageId, assessment.InvoiceId, assessment.LineId,
    task.taskSuggestionId AS TaskSuggestionId, task.taskCode AS SuggestedTaskCode,
    asset.assetClassSuggestionId, asset.assetClassCode AS SuggestedAssetClassCode,
    CASE WHEN asset.assetClassSuggestionId IS NOT NULL THEN 'ASSET_CLASS' ELSE 'TASK' END AS ClassificationType,
    COALESCE(task.ItemIdentified, asset.ItemIdentified) AS ItemIdentified,
    task.MatchMethod, COALESCE(task.confidence, asset.confidence) AS ClassificationConfidence,
    COALESCE(task.resultStatus, asset.resultStatus) AS ClassificationResolutionStatus,
    COALESCE(task.UnresolvedReason, asset.UnresolvedReason) AS ClassificationReviewReason,
    COALESCE(task.PotentialBundling, asset.PotentialBundling) AS PotentialBundling,
    assessment.RepairMaintenanceFlag, assessment.AroFlag, assessment.PotentialTagsJson,
    assessment.CategorySuggestion, category.CategoryName,
    category.ReviewConsiderations, category.SuggestedAction,
    assessment.NatureResult, assessment.Recommendation,
    assessment.OutcomeCategory AS RecommendationBasis,
    assessment.ThresholdAmount, assessment.ThresholdCurrency,
    assessment.BundlingDisposition, assessment.MissingInputs,
    assessment.ReviewReasons, assessment.Rationale,
    assessment.UsedLlm, assessment.ModelVersion
FROM capex.Assessment AS assessment
LEFT JOIN ref.AssessmentOutcomeCategory AS category
    ON category.CategoryCode=assessment.CategorySuggestion
LEFT JOIN task.TaskSuggestion AS task
    ON task.taskSuggestionId = assessment.TaskSuggestionID
    AND task.correlationId = assessment.WorkflowRunId
    AND task.packageId = assessment.PackageId
    AND task.invoiceId = assessment.InvoiceId
    AND task.lineId = assessment.LineId
LEFT JOIN task.assetClassSuggestion AS asset
    ON asset.assetClassSuggestionId = assessment.AssetClassSuggestionID
    AND asset.correlationId = assessment.WorkflowRunId AND asset.packageId = assessment.PackageId
    AND asset.invoiceId = assessment.InvoiceId AND asset.lineId = assessment.LineId
WHERE assessment.WorkflowRunId = @WorkflowRunId
ORDER BY assessment.InvoiceId, assessment.LineId;

SELECT JSON_VALUE(EvidenceJson, '$.demoRecommendations') AS ConditionalDemoMode,
    JSON_VALUE(EvidenceJson, '$.governedSqlReferences') AS GovernedSqlReferences,
    JSON_VALUE(EvidenceJson, '$.bundlingDeferred') AS BundlingDeferred,
    JSON_VALUE(EvidenceJson, '$.sqlPolicyReferencesSha256') AS SqlPolicyReferencesSha256,
    JSON_QUERY(EvidenceJson, '$.demoAssumptions') AS DisclosedDemoAssumptions
FROM capex.LlmShowcaseRun WHERE WorkflowRunId = @WorkflowRunId;

SELECT PackageId, InvoiceId, InvoiceLineId, ModelDecision, BundleGroupId,
    IsOneAssetSuggestion, Confidence, Rationale, UsedLlm, ModelVersion
FROM capex.BundleDecision WHERE WorkflowRunId = @WorkflowRunId
ORDER BY InvoiceId, InvoiceLineId;

SELECT * FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId;
SELECT * FROM capex.BundleCandidateMember WHERE WorkflowRunId = @WorkflowRunId;

SELECT calls.stage AS ModelStage, COUNT_BIG(*) AS Attempts,
    COUNT_BIG(CASE WHEN calls.response IS NOT NULL AND calls.error IS NULL THEN 1 END) AS Responses,
    COUNT_BIG(CASE WHEN calls.response IS NOT NULL AND calls.error IS NULL
        AND calls.validation_error IS NULL THEN 1 END) AS AcceptedResponses
FROM capex.LlmShowcaseRun AS evidence
CROSS APPLY OPENJSON(evidence.EvidenceJson, '$.calls')
WITH (
    stage nvarchar(64) '$.stage', response nvarchar(max) '$.response',
    error nvarchar(max) '$.error', validation_error nvarchar(max) '$.validationError'
) AS calls
WHERE evidence.WorkflowRunId = @WorkflowRunId
GROUP BY calls.stage;

SELECT [key] AS NativeCurrency,
    JSON_VALUE(value, '$.policyId') AS DemoPolicyId,
    JSON_VALUE(value, '$.approvalStatus') AS ApprovalStatus,
    JSON_VALUE(value, '$.capitalization.baseThreshold') AS DemoUnitThreshold
FROM OPENJSON(@Payload, '$.policySnapshots');
