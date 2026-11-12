SET NOCOUNT ON;

-- Read-only. Set the ADF RunId after its named job execution and batch.json succeed.
-- Do not use Friday's frozen workflow ID or a failed run's review.json candidate.
DECLARE @WorkflowRunId nvarchar(128) = N'REPLACE_WITH_ADF_RUN_ID';
IF @WorkflowRunId = N'REPLACE_WITH_ADF_RUN_ID'
    THROW 51220, 'Set the completed ADF RunId before running this verification.', 1;

DECLARE @Payload nvarchar(max) = (
    SELECT payload FROM mvp_review.imports WHERE run_id = @WorkflowRunId
);
IF @Payload IS NULL OR ISJSON(@Payload) <> 1
    THROW 51221, 'No committed analyst-review handoff exists for this run.', 1;

IF JSON_VALUE(@Payload, '$.schemaVersion') <> '1.0-capex-adf-review'
    OR JSON_VALUE(@Payload, '$.workflowRunId') <> @WorkflowRunId
    OR JSON_VALUE(@Payload, '$.synthetic') <> 'false'
    OR JSON_VALUE(@Payload, '$.businessApproved') <> 'false'
    OR JSON_VALUE(@Payload, '$.reviewStatus') <> 'AWAITING_ANALYST'
    OR JSON_VALUE(@Payload, '$.partitions[0].result.status') <> 'SUCCEEDED'
    OR JSON_VALUE(@Payload, '$.popaWrites') <> '0'
    OR JSON_VALUE(@Payload, '$.schemaVersion') IS NULL
    OR JSON_VALUE(@Payload, '$.workflowRunId') IS NULL
    OR JSON_VALUE(@Payload, '$.synthetic') IS NULL
    OR JSON_VALUE(@Payload, '$.businessApproved') IS NULL
    OR JSON_VALUE(@Payload, '$.reviewStatus') IS NULL
    OR JSON_VALUE(@Payload, '$.partitions[0].result.status') IS NULL
    OR JSON_VALUE(@Payload, '$.popaWrites') IS NULL
    THROW 51222, 'The handoff is not a completed, original-source, unapproved ADF result.', 1;

DECLARE @ExpectedLines bigint = (
    SELECT SUM(line_count) FROM OPENJSON(@Payload, '$.partitions[0].result.packages')
    WITH (line_count int '$.lineCount')
);
DECLARE @ExpectedDocuments bigint = (SELECT COUNT_BIG(*) FROM OPENJSON(@Payload, '$.documents'));
DECLARE @ModelLines bigint = (
    SELECT COUNT_BIG(*) FROM OPENJSON(@Payload, '$.partitions[0].result.assessments')
    WITH (used_llm bit '$.usedLlm') WHERE used_llm = 1
);
DECLARE @BundlingDeferred bit = CASE
    WHEN JSON_VALUE(@Payload, '$.partitions[0].evidence.bundlingDeferred')='true' THEN 1 ELSE 0 END;
DECLARE @ExpectedBundleDecisions bigint = CASE WHEN @BundlingDeferred=1 THEN 0 ELSE @ModelLines END;
IF @ExpectedLines IS NULL OR @ExpectedLines < 1 OR @ExpectedDocuments < 1
    OR TRY_CONVERT(bigint, JSON_VALUE(@Payload, '$.sqlWrites')) IS NULL
    OR TRY_CONVERT(bigint, JSON_VALUE(@Payload, '$.sqlWrites')) <> 2 * @ExpectedLines
    THROW 51223, 'The handoff line/document counts are missing or inconsistent.', 1;

DECLARE @Suggestions TABLE (
    taskSuggestionId uniqueidentifier, correlationId nvarchar(256), packageId nvarchar(256),
    invoiceId nvarchar(256), lineId nvarchar(256), ClassificationType varchar(16),
    modelDeployment nvarchar(256), MatchMethod varchar(32),
    PotentialBundling bit
);
INSERT INTO @Suggestions
SELECT taskSuggestionId, correlationId, packageId, invoiceId, lineId, 'TASK', modelDeployment, MatchMethod,
    PotentialBundling
FROM task.TaskSuggestion WHERE correlationId = @WorkflowRunId
UNION ALL
SELECT assetClassSuggestionId, correlationId, packageId, invoiceId, lineId, 'ASSET_CLASS',
    COALESCE(modelDeployment, 'NOT_CALLED_REFERENCE_REVIEW'),
    CASE WHEN resultStatus = 'REVIEW' THEN 'REVIEW' ELSE 'LANGCHAIN' END,
    PotentialBundling
FROM task.assetClassSuggestion WHERE correlationId = @WorkflowRunId;

IF (SELECT COUNT_BIG(*) FROM @Suggestions) <> @ExpectedLines
    OR (SELECT COUNT_BIG(*) FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId) <> @ExpectedLines
    OR (SELECT COUNT_BIG(*) FROM capex.BundleDecision WHERE WorkflowRunId = @WorkflowRunId) <> @ExpectedBundleDecisions
    OR (SELECT COUNT_BIG(*) FROM mvp_review.documents WHERE run_id = @WorkflowRunId) <> @ExpectedDocuments
    THROW 51224, 'SQL tasks, assessments, model bundle decisions or review documents do not reconcile.', 1;

IF EXISTS (
    SELECT 1 FROM OPENJSON(@Payload, '$.partitions[0].result.assessments')
    WITH (
        package_id nvarchar(128) '$.packageId', invoice_id nvarchar(128) '$.invoiceId',
        line_id nvarchar(256) '$.lineId', used_llm bit '$.usedLlm'
    ) AS expected
    WHERE (SELECT COUNT_BIG(*) FROM capex.Assessment AS assessment
        JOIN @Suggestions AS task ON task.taskSuggestionId =
            CASE WHEN task.ClassificationType = 'TASK' THEN assessment.TaskSuggestionID
                 ELSE assessment.AssetClassSuggestionID END
          AND task.correlationId = assessment.WorkflowRunId AND task.packageId = assessment.PackageId
          AND task.invoiceId = assessment.InvoiceId AND task.lineId = assessment.LineId
        WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.PackageId = expected.package_id
          AND assessment.InvoiceId = expected.invoice_id AND assessment.LineId = expected.line_id
          AND assessment.UsedLlm = expected.used_llm) <> 1
)
    THROW 51225, 'A recommendation is missing, duplicated, misattributed or not linked to its task suggestion.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    JOIN @Suggestions AS task ON task.taskSuggestionId =
        CASE WHEN task.ClassificationType = 'TASK' THEN assessment.TaskSuggestionID
             ELSE assessment.AssetClassSuggestionID END
    WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.UsedLlm = 0 AND (
        assessment.Recommendation <> 'REVIEW' OR assessment.BundlingDisposition <> 'REVIEW'
        OR assessment.ModelVersion IS NOT NULL OR task.modelDeployment <> 'NOT_CALLED_REFERENCE_REVIEW'
        OR task.MatchMethod <> 'REVIEW' OR task.modelDeployment IS NULL OR task.MatchMethod IS NULL
        OR EXISTS (SELECT 1 FROM capex.BundleDecision AS decision
            WHERE decision.WorkflowRunId = assessment.WorkflowRunId AND decision.PackageId = assessment.PackageId
              AND decision.InvoiceLineId = assessment.LineId)
    )
)
    THROW 51226, 'Non-model exceptions must remain Review and must not be attributed to GPT.', 1;

IF EXISTS (
    SELECT 1 FROM capex.Assessment AS assessment
    JOIN @Suggestions AS task ON task.taskSuggestionId =
        CASE WHEN task.ClassificationType = 'TASK' THEN assessment.TaskSuggestionID
             ELSE assessment.AssetClassSuggestionID END
    WHERE assessment.WorkflowRunId = @WorkflowRunId AND assessment.UsedLlm = 1 AND (
        assessment.ModelVersion IS NULL OR assessment.ModelVersion <> 'gpt-5.1'
        OR task.modelDeployment IS NULL OR task.modelDeployment <> 'gpt-5.1'
        OR (SELECT COUNT_BIG(*) FROM capex.BundleDecision AS decision
            WHERE decision.WorkflowRunId = assessment.WorkflowRunId AND decision.PackageId = assessment.PackageId
              AND decision.InvoiceId = assessment.InvoiceId AND decision.InvoiceLineId = assessment.LineId)
                 <> CASE WHEN @BundlingDeferred=1 THEN 0 ELSE 1 END
    )
)
    THROW 51227, 'Model-backed recommendations do not have corresponding GPT bundle decisions.', 1;

IF JSON_VALUE(@Payload, '$.partitions[0].evidence.classificationRouting') IS NOT NULL AND EXISTS (
    SELECT 1
    FROM OPENJSON(@Payload, '$.sourceManifest.sources')
    WITH (file_name nvarchar(512) '$.fileName', package_ids nvarchar(max) '$.packageIds' AS JSON) AS source
    CROSS APPLY OPENJSON(source.package_ids) AS package
    JOIN @Suggestions AS suggestion ON suggestion.packageId = package.value
    WHERE suggestion.ClassificationType <> CASE WHEN LOWER(source.file_name) LIKE '%.xlsx'
        THEN 'ASSET_CLASS' ELSE 'TASK' END
)
    THROW 51232, 'EU XLSX invoices require asset-class suggestions; NA ZIP invoices require task suggestions.', 1;

IF EXISTS (
    SELECT 1 FROM task.TaskSuggestion AS suggestion
    LEFT JOIN pub.v1_TaskMaster AS master
        ON master.taskCode=suggestion.taskCode AND master.regionCode=N'US'
    WHERE suggestion.correlationId=@WorkflowRunId AND suggestion.taskCode IS NOT NULL
        AND (master.taskCode IS NULL OR suggestion.assetClassCode IS NULL
            OR suggestion.assetClassCode<>master.description
            OR suggestion.SuggestedAssetClass IS NULL
            OR suggestion.SuggestedAssetClass<>master.description)
)
    THROW 51238, 'NA task labels must equal the selected TaskMaster description, not its asset categories.', 1;

IF JSON_VALUE(@Payload, '$.partitions[0].evidence.governedSqlReferences')='true'
BEGIN
    IF @BundlingDeferred<>1 OR EXISTS (
        SELECT 1 FROM capex.BundleCandidateGroup WHERE WorkflowRunId=@WorkflowRunId
    ) OR EXISTS (
        SELECT 1 FROM capex.Assessment WHERE WorkflowRunId=@WorkflowRunId
            AND (CategorySuggestion='CAPEX_POTENTIAL_BUNDLING' OR BundlingDisposition<>'REVIEW')
    )
        THROW 51235, 'SQL-governed demo must retain deferred bundling without fabricated model decisions/groups.', 1;
    IF EXISTS (
        SELECT 1 FROM OPENJSON(@Payload, '$.partitions[0].result.assessments')
        WITH (package_id nvarchar(128) '$.packageId', invoice_id nvarchar(128) '$.invoiceId',
            line_id nvarchar(256) '$.lineId', category nvarchar(64) '$.categorySuggestion') AS expected
        JOIN capex.Assessment AS assessment ON assessment.WorkflowRunId=@WorkflowRunId
            AND assessment.PackageId=expected.package_id AND assessment.InvoiceId=expected.invoice_id
            AND assessment.LineId=expected.line_id
        LEFT JOIN ref.AssessmentOutcomeCategory AS category
            ON category.CategoryCode=assessment.CategorySuggestion
        WHERE ISNULL(assessment.CategorySuggestion,N'')<>ISNULL(expected.category,N'')
            OR (assessment.CategorySuggestion IS NOT NULL AND ISNULL(category.EnabledForDemo,0)<>1)
    )
        THROW 51236, 'Assessment categories differ from run evidence or use disabled catalog entries.', 1;
    IF LEN(JSON_VALUE(@Payload, '$.partitions[0].evidence.sqlPolicyReferencesSha256'))<>64
        OR JSON_VALUE(@Payload, '$.partitions[0].evidence.sqlPolicyReferencesSha256') IS NULL
        OR JSON_VALUE(@Payload, '$.partitions[0].evidence.demoAssumptions.missingUnitCostBasis')
             <>'REVIEW_NO_LINE_AMOUNT_PROXY'
        THROW 51237, 'SQL rule provenance or explicit missing-unit-cost handling is absent.', 1;
END;

IF JSON_VALUE(@Payload, '$.partitions[0].evidence.potentialReviewTags.promptVersion') IS NOT NULL
BEGIN
    IF EXISTS (SELECT 1 FROM capex.Assessment WHERE WorkflowRunId=@WorkflowRunId
        AND (PotentialTagsJson IS NULL OR ISJSON(PotentialTagsJson) <> 1
            OR RepairMaintenanceFlag IS NULL OR AroFlag IS NULL))
        OR EXISTS (SELECT 1 FROM @Suggestions WHERE PotentialBundling IS NULL)
        THROW 51233, 'New runs require a recorded potential-tag screening outcome for every line.', 1;
    IF EXISTS (
        SELECT 1 FROM capex.Assessment AS assessment
        JOIN @Suggestions AS suggestion
            ON suggestion.correlationId=assessment.WorkflowRunId
            AND suggestion.packageId=assessment.PackageId AND suggestion.invoiceId=assessment.InvoiceId
            AND suggestion.lineId=assessment.LineId
        CROSS APPLY (VALUES
            ('POTENTIAL_BUNDLING', suggestion.PotentialBundling),
            ('POTENTIAL_REPAIR_MAINTENANCE_OPEX', assessment.RepairMaintenanceFlag),
            ('POTENTIAL_ARO', assessment.AroFlag)
        ) AS signal(Tag, Flag)
        WHERE assessment.WorkflowRunId=@WorkflowRunId AND signal.Flag <> CASE WHEN EXISTS (
            SELECT 1 FROM OPENJSON(assessment.PotentialTagsJson)
            WITH (Tag nvarchar(64) '$.tag') AS evidence WHERE evidence.Tag=signal.Tag
        ) THEN 1 ELSE 0 END
    )
        THROW 51234, 'Potential flags do not match their recorded tag evidence.', 1;
END;

IF (SELECT COUNT_BIG(*) FROM capex.LlmShowcaseRun WHERE WorkflowRunId = @WorkflowRunId)
    <> CASE WHEN @ModelLines > 0 THEN 1 ELSE 0 END
    OR (@ModelLines > 0 AND NOT EXISTS (
        SELECT 1 FROM capex.LlmShowcaseRun WHERE WorkflowRunId = @WorkflowRunId
          AND Status = 'SUCCEEDED'
          AND JSON_VALUE(EvidenceJson, '$.taskMasterSource') = 'pub.v1_TaskMaster'
          AND JSON_VALUE(EvidenceJson, '$.freshPdfExtraction') = 'true'
          AND JSON_VALUE(EvidenceJson, '$.provenance') =
              N'ORIGINAL_SOURCE_DEMO_WITH_UNAPPROVED_POLICY:' + @WorkflowRunId
    ))
    THROW 51228, 'Original-source model evidence and SQL TaskMaster provenance are missing.', 1;

IF EXISTS (
    SELECT 1 FROM mvp_review.documents WHERE run_id = @WorkflowRunId
      AND LOWER(CONVERT(varchar(64), HASHBYTES('SHA2_256', content), 2)) <> LOWER(sha)
)
    THROW 51229, 'Persisted review document bytes do not match their source hashes.', 1;

SELECT N'ADF_SQL_REVIEW_VERIFIED' AS ValidationResult, @WorkflowRunId AS WorkflowRunId,
    @ExpectedDocuments AS ReviewDocuments, @ExpectedLines AS TaskAndAssessmentLines,
    @ExpectedBundleDecisions AS ModelBundleDecisions, @BundlingDeferred AS BundlingDeferred,
    @ExpectedLines - @ModelLines AS NonModelReviewLines;
SELECT PackageId, InvoiceId, COUNT_BIG(*) AS Lines,
    SUM(CASE WHEN Recommendation = 'CAPEX' THEN 1 ELSE 0 END) AS CapEx,
    SUM(CASE WHEN Recommendation = 'OPEX' THEN 1 ELSE 0 END) AS OpEx,
    SUM(CASE WHEN Recommendation = 'REVIEW' THEN 1 ELSE 0 END) AS ReviewRequired
FROM capex.Assessment WHERE WorkflowRunId = @WorkflowRunId GROUP BY PackageId, InvoiceId;
SELECT * FROM capex.BundleCandidateGroup WHERE WorkflowRunId = @WorkflowRunId;
SELECT run_id, document_id, version, created_at FROM mvp_review.decisions
WHERE run_id = @WorkflowRunId ORDER BY document_id, version;
