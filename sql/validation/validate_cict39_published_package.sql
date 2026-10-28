/*
CICT-39 validation: pub.v1_InvoicePackage and minimum enrichment.

Run with the operator's read-only contained-database identity against
cict-ai-cus-sqldb. Every statement is read-only.
*/

SET NOCOUNT ON;

-- 1. Confirm the published view exists.
SELECT
    s.name AS schema_name,
    v.name AS view_name
FROM sys.views AS v
INNER JOIN sys.schemas AS s
    ON s.schema_id = v.schema_id
WHERE s.name = N'pub'
  AND v.name = N'v1_InvoicePackage';

-- Expected: exactly one row.

-- 2. Inspect recent published packages and their minimum context.
SELECT TOP (20)
    packageId,
    runId,
    schemaVersion,
    JSON_VALUE(invoice, '$.invoiceNumber') AS invoiceNumber,
    JSON_VALUE(invoice, '$.vendorName') AS vendorName,
    JSON_VALUE(invoice, '$.currency') AS currency,
    JSON_VALUE(invoice, '$.poNumber') AS poNumber,
    JSON_VALUE(context, '$.businessUnit') AS businessUnit,
    JSON_VALUE(context, '$.projectStatus') AS projectStatus,
    JSON_VALUE(context, '$.projectType') AS projectType,
    JSON_VALUE(context, '$.bundlingInScope') AS bundlingInScope,
    JSON_VALUE(dataQuality, '$.missingPo') AS missingPo,
    JSON_VALUE(dataQuality, '$.missingProject') AS missingProject,
    JSON_VALUE(dataQuality, '$.missingSite') AS missingSite,
    JSON_VALUE(source, '$.packageName') AS sourcePackageName,
    JSON_VALUE(source, '$.sourceSha256') AS sourceSha256,
    (
        SELECT COUNT_BIG(1)
        FROM OPENJSON(lines)
    ) AS lineCount,
    createdAtUtc
FROM pub.v1_InvoicePackage
ORDER BY createdAtUtc DESC;

-- Expected:
--   * schemaVersion is 1.0;
--   * every package has at least one line;
--   * optional PO/project/site values may be NULL;
--   * corresponding data-quality flags describe missing context.

-- 3. Inspect line-level minimum enrichment.
SELECT TOP (100)
    package.packageId,
    package.runId,
    line.invoiceLineId,
    line.lineNumber,
    line.poLineNumber,
    line.poDescription,
    line.poTaskCode,
    line.afeProjectId,
    line.siteId
FROM pub.v1_InvoicePackage AS package
CROSS APPLY OPENJSON(package.lines)
WITH
(
    invoiceLineId nvarchar(256) '$.invoiceLineId',
    lineNumber int '$.lineNumber',
    poLineNumber int '$.poLineNumber',
    poDescription nvarchar(2000) '$.poDescription',
    poTaskCode nvarchar(256) '$.poTaskCode',
    afeProjectId nvarchar(256) '$.afeProjectId',
    siteId nvarchar(256) '$.siteId'
) AS line
ORDER BY package.createdAtUtc DESC, line.lineNumber;

-- 4. Prove missing optional context does not remove the package.
SELECT TOP (20)
    packageId,
    runId,
    JSON_VALUE(invoice, '$.invoiceNumber') AS invoiceNumber,
    JSON_VALUE(dataQuality, '$.missingPo') AS missingPo,
    JSON_VALUE(dataQuality, '$.missingProject') AS missingProject,
    JSON_VALUE(dataQuality, '$.missingSite') AS missingSite,
    (
        SELECT COUNT_BIG(1)
        FROM OPENJSON(lines)
    ) AS retainedLineCount
FROM pub.v1_InvoicePackage
WHERE JSON_VALUE(dataQuality, '$.missingPo') = 'true'
   OR JSON_VALUE(dataQuality, '$.missingProject') = 'true'
   OR JSON_VALUE(dataQuality, '$.missingSite') = 'true'
ORDER BY createdAtUtc DESC;

-- Expected when suitable sample data exists:
-- packages with missing optional context are returned with retainedLineCount > 0.

-- 5. These integrity checks must return zero rows.

-- No published package may contain zero lines.
SELECT
    packageId,
    runId
FROM pub.v1_InvoicePackage
WHERE NOT EXISTS
(
    SELECT 1
    FROM OPENJSON(lines)
);

-- Published package IDs must be unique.
SELECT
    packageId,
    COUNT_BIG(1) AS duplicateCount
FROM pub.v1_InvoicePackage
GROUP BY packageId
HAVING COUNT_BIG(1) > 1;

-- The v1 view must expose only the supported schema version.
SELECT
    packageId,
    schemaVersion
FROM pub.v1_InvoicePackage
WHERE schemaVersion <> '1.0'
   OR schemaVersion IS NULL;
