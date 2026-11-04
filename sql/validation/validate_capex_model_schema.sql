SET NOCOUNT ON;

DECLARE @Model TABLE
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    ColumnNames nvarchar(max) NOT NULL,
    PRIMARY KEY (SchemaName, TableName)
);

INSERT INTO @Model VALUES
    (N'prep', N'Invoice', N'InvoiceID|InvoiceNumber|InvoiceDate|VendorID|VendorName|InvoiceCurrency|InvoiceTotal|PONumber|CountryRegion|SourceSystem|NuvoloWorkOrderNo|HasLineItems|RunID|LoadedAtUtc'),
    (N'prep', N'InvoicePackage', N'PackageID|InvoiceID|RunID|SchemaVersion|BundlingEligible|PackageStatus|CreatedAtUtc'),
    (N'prep', N'CodingDistribution', N'ApprovedCodingDistributionID|InvoiceID|CodedTaskCode|CodedTaskDescription|CodedAssetClass|DistributionAmount|ApproverID|ApprovalDate'),
    (N'prep', N'InvoiceLine', N'InvoiceLineID|InvoiceID|LineNumber|LineDescription|PartNumber|Quantity|UnitCost|LineAmount|POLineNumber|SiteID|AFEProjectID'),
    (N'prep', N'Project', N'AFEProjectID|ProjectDescription|ProjectTotalBudget|ProjectStatus|ProjectType|CountryRegion|ProjectStartDate|BundlingInScope'),
    (N'prep', N'Site', N'SiteID|CountryRegion|IsLeased'),
    (N'prep', N'PurchaseOrderLine', N'PONumber|POLineNumber|PODescription|AFEProjectID|SiteID|POTaskCode|BusinessUnit|VendorID'),
    (N'prep', N'PurchaseOrder', N'PONumber|VendorID|AFEProjectID|POStatus|PODescription|CreatedAtUtc|LastUpdatedAtUtc'),
    (N'task', N'TaskSuggestion', N'TaskSuggestionID|InvoiceLineID|PackageID|RunID|ItemIdentified|SuggestedTaskCode|TaskMasterVersion|SuggestedAssetClass|MatchMethod|PartMasterID|Confidence|ResolutionStatus|UnresolvedReason|Evidence|ModelVersion|PromptVersion|ContractVersion|CreatedAtUtc'),
    (N'capex', N'Assessment', N'AssessmentID|InvoiceLineID|TaskSuggestionID|ThresholdID|RunID|NatureResult|RepairMaintenanceIndicator|NeverCapitalizeHit|UnitCostMeetsThreshold|CapExOpExSuggestion|OutcomeCategory|NuvoloRiskFlag|MissingSiteFlag|LeasedSiteFlag|BundleGroupID|Rationale|PolicyCitations|UsedLLM|ModelVersion|ContractVersion|CreatedAtUtc'),
    (N'capex', N'BundleCandidateGroup', N'BundleGroupID|AFEProjectID|SiteID|TaskCode|GroupSeq|ThresholdID|CombinedValue|MemberCount|GroupStatus|LastEvaluatedRunID|UpdatedAtUtc'),
    (N'capex', N'BundleCandidateMember', N'BundleGroupID|InvoiceLineID|LineValue|AddedRunID|AddedAtUtc'),
    (N'review', N'ReviewDecision', N'DecisionID|ReviewCaseID|InvoiceLineID|AssessmentID|TaskSuggestionID|FinalTaskCode|FinalCapExOpEx|FinalOutcomeCategory|ConfirmedItem|DecisionType|OverrideReason|ReviewerID|DecidedAtUtc|DecisionVersion|IsCurrent'),
    (N'review', N'BundleDecision', N'BundleDecisionID|BundleGroupID|ReviewCaseID|IsOneAsset|Reason|ReviewerID|DecidedAtUtc'),
    (N'review', N'ReviewCase', N'ReviewCaseID|InvoiceID|CaseStatus|AssignedTo|CreatedFromEventID|RowVersion|CreatedAtUtc|UpdatedAtUtc'),
    (N'review', N'ApprovedCoding', N'ApprovedCodingID|ReviewCaseID|InvoiceID|TaskCode|DistributionAmount|DecisionVersion'),
    (N'review', N'POPAHandoff', N'HandoffID|ReviewCaseID|HandoffMethod|HandoffStatus|AckReference|UpdatedAtUtc'),
    (N'ops', N'Outbox', N'OutboxID|EventType|SchemaVersion|InvoiceID|PayloadJson|OwnerComponent|CreatedAtUtc|PublishedAtUtc|IdempotencyKey'),
    (N'ref', N'TaskMaster', N'TaskCode|TaskMasterVersion|TaskDescription|GLAccount|AssetType|IsCapitalTask|EffectiveFrom|EffectiveTo'),
    (N'ref', N'PartMaster', N'PartMasterID|VendorID|PartNumber|Version|ItemDescription|MappedTaskCode|MappedAssetClass|Source|ConfirmedBy|ConfirmedAtUtc|IsCurrent|ConflictStatus'),
    (N'ref', N'CapitalizationThreshold', N'ThresholdID|GovernedThreshold|ThresholdType|CountryRegion|Currency|ProjectBudgetMin|CapitalizationPolicyReference|IFRSReference|EffectiveFrom|EffectiveTo'),
    (N'ref', N'NeverCapitalizeException', N'ExceptionID|ItemCategory|Keyword|PolicyReference|Active'),
    (N'ref', N'RepairMaintenanceIndicator', N'IndicatorID|Keyword|Category|SignalStrength|PolicyReference');

DECLARE @Expected TABLE (SchemaName sysname, TableName sysname, ColumnName sysname);
INSERT INTO @Expected
SELECT model.SchemaName, model.TableName, columnName.value
FROM @Model AS model
CROSS APPLY STRING_SPLIT(model.ColumnNames, N'|') AS columnName;

SELECT
    expected.SchemaName,
    expected.TableName,
    expected.ColumnName,
    CASE
        WHEN object_id.objectId IS NULL THEN 'MISSING_TABLE'
        WHEN actual.column_id IS NULL THEN 'MISSING_COLUMN'
        ELSE 'PRESENT'
    END AS CoverageStatus,
    TYPE_NAME(actual.system_type_id) AS ActualDataType,
    CASE
        WHEN actual.max_length = -1 THEN -1
        WHEN TYPE_NAME(actual.system_type_id) IN ('nvarchar', 'nchar') THEN actual.max_length / 2
        ELSE actual.max_length
    END AS ActualMaxLength,
    actual.precision AS ActualPrecision,
    actual.scale AS ActualScale,
    actual.is_nullable AS IsNullable,
    actual.is_identity AS IsIdentity,
    actual.is_computed AS IsComputed,
    computed.definition AS ComputedDefinition
FROM @Expected AS expected
CROSS APPLY (VALUES (OBJECT_ID(QUOTENAME(expected.SchemaName) + N'.' + QUOTENAME(expected.TableName), N'U')))
    AS object_id(objectId)
LEFT JOIN sys.columns AS actual
    ON actual.object_id = object_id.objectId AND actual.name = expected.ColumnName
LEFT JOIN sys.computed_columns AS computed
    ON computed.object_id = actual.object_id AND computed.column_id = actual.column_id
ORDER BY expected.SchemaName, expected.TableName, expected.ColumnName;

IF EXISTS
(
    SELECT 1 FROM @Expected AS expected
    WHERE NOT EXISTS
    (
        SELECT 1 FROM sys.columns AS actual
        WHERE actual.object_id = OBJECT_ID(QUOTENAME(expected.SchemaName) + N'.' + QUOTENAME(expected.TableName), N'U')
          AND actual.name = expected.ColumnName
    )
)
    THROW 51120, 'The v2.1 model is missing tables or columns. Inspect coverage results; apply migration 009.', 1;

SELECT
    DB_NAME() AS DatabaseName,
    (SELECT COUNT(*) FROM @Model) AS ModelTableCount,
    (SELECT COUNT(*) FROM @Expected) AS ModelColumnCount,
    'COLUMN_COVERAGE_PASS' AS CoverageStatus,
    'Runtime-compatible physical model; see sql/README.md for key, type, nullability and relationship deviations.' AS Scope,
    SYSUTCDATETIME() AS CheckedAtUtc;

-- Return physical constraints for DBA review; column coverage alone is not model equivalence.
SELECT
    schemaName.name AS SchemaName,
    tableName.name AS TableName,
    fk.name AS ForeignKeyName,
    fk.is_disabled AS IsDisabled,
    fk.is_not_trusted AS IsNotTrusted,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS ReferencedSchema,
    OBJECT_NAME(fk.referenced_object_id) AS ReferencedTable
FROM sys.foreign_keys AS fk
JOIN sys.tables AS tableName ON tableName.object_id = fk.parent_object_id
JOIN sys.schemas AS schemaName ON schemaName.schema_id = tableName.schema_id
JOIN @Model AS model ON model.SchemaName = schemaName.name AND model.TableName = tableName.name
ORDER BY schemaName.name, tableName.name, fk.name;
