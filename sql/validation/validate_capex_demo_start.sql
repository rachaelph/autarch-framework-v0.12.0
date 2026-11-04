SET NOCOUNT ON;

SELECT
    DB_NAME() AS DatabaseName,
    USER_NAME() AS DatabaseUser,
    SYSUTCDATETIME() AS CheckedAtUtc;

SELECT
    COUNT_BIG(*) AS ExistingTaskSuggestions,
    COUNT_BIG(DISTINCT packageId) AS ExistingTaskPackages
FROM task.TaskSuggestion;

SELECT
    packageId,
    invoiceId,
    eventId,
    modelDeployment,
    resultStatus,
    COUNT_BIG(*) AS LineCount
FROM task.TaskSuggestion
GROUP BY packageId, invoiceId, eventId, modelDeployment, resultStatus
ORDER BY packageId, eventId, resultStatus;

-- A cleanup must stop rather than cascade through referenced evidence.
SELECT
    (SELECT COUNT_BIG(*) FROM capex.Assessment
        WHERE TaskSuggestionID IS NOT NULL) AS AssessmentsReferencingTasks,
    (SELECT COUNT_BIG(*) FROM review.ReviewDecision) AS HumanReviewDecisions,
    (SELECT COUNT_BIG(*) FROM capex.AssessmentRun) AS ExistingAssessmentRuns;

SELECT
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS ReferencingSchema,
    OBJECT_NAME(fk.parent_object_id) AS ReferencingTable,
    fk.name AS ForeignKeyName,
    fk.is_disabled AS IsDisabled,
    fk.is_not_trusted AS IsNotTrusted
FROM sys.foreign_keys AS fk
WHERE fk.referenced_object_id = OBJECT_ID(N'task.TaskSuggestion')
ORDER BY ReferencingSchema, ReferencingTable;

SELECT
    package.PackageId,
    package.InvoiceId,
    package.InvoiceNumber,
    package.VendorName,
    package.PackageStatus,
    (SELECT COUNT_BIG(*) FROM prep.InvoiceLine AS line
        WHERE line.PackageId = package.PackageId) AS PreparedLineCount
FROM prep.InvoicePackage AS package
ORDER BY package.VendorName, package.PackageId;

SELECT regionCode, COUNT_BIG(*) AS CurrentActiveTaskCount
FROM pub.v1_TaskMaster
GROUP BY regionCode
ORDER BY regionCode;

SELECT 'prep.Invoice' AS ObjectName, COUNT_BIG(*) AS [RowCount] FROM prep.Invoice
UNION ALL SELECT 'prep.Project', COUNT_BIG(*) FROM prep.Project
UNION ALL SELECT 'prep.Site', COUNT_BIG(*) FROM prep.Site
UNION ALL SELECT 'prep.PurchaseOrder', COUNT_BIG(*) FROM prep.PurchaseOrder
UNION ALL SELECT 'prep.PurchaseOrderLine', COUNT_BIG(*) FROM prep.PurchaseOrderLine
UNION ALL SELECT 'ref.PartMaster', COUNT_BIG(*) FROM ref.PartMaster
UNION ALL SELECT 'ref.CapitalizationThreshold', COUNT_BIG(*) FROM ref.CapitalizationThreshold
UNION ALL SELECT 'ref.NeverCapitalizeException', COUNT_BIG(*) FROM ref.NeverCapitalizeException
UNION ALL SELECT 'ref.RepairMaintenanceIndicator', COUNT_BIG(*) FROM ref.RepairMaintenanceIndicator
UNION ALL SELECT 'review.ReviewCase', COUNT_BIG(*) FROM review.ReviewCase
UNION ALL SELECT 'review.ApprovedCoding', COUNT_BIG(*) FROM review.ApprovedCoding
UNION ALL SELECT 'review.BundleDecision', COUNT_BIG(*) FROM review.BundleDecision
UNION ALL SELECT 'review.POPAHandoff', COUNT_BIG(*) FROM review.POPAHandoff
UNION ALL SELECT 'ops.Outbox', COUNT_BIG(*) FROM ops.Outbox;
