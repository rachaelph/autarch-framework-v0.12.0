SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

CREATE OR ALTER VIEW pub.v1_InvoicePackage
AS
    SELECT
        p.SchemaVersion AS schemaVersion,
        p.PackageId AS packageId,
        p.RunId AS runId,

        JSON_QUERY
        (
            (
                SELECT
                    p.InvoiceId AS invoiceId,
                    p.InvoiceNumber AS invoiceNumber,
                    p.InvoiceDate AS invoiceDate,
                    p.VendorId AS vendorId,
                    p.VendorName AS vendorName,
                    p.Currency AS currency,
                    p.InvoiceTotal AS invoiceTotal,
                    p.PoNumber AS poNumber,
                    p.CountryRegion AS countryRegion,
                    p.SourceSystem AS sourceSystem
                FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
            )
        ) AS invoice,

        JSON_QUERY
        (
            (
                SELECT
                    line.InvoiceLineId AS invoiceLineId,
                    line.LineNumber AS lineNumber,
                    line.Description AS description,
                    line.PartNumber AS partNumber,
                    line.Quantity AS quantity,
                    line.UnitCost AS unitCost,
                    line.LineAmount AS lineAmount,
                    line.PoLineNumber AS poLineNumber,
                    line.PoDescription AS poDescription,
                    line.PoTaskCode AS poTaskCode,
                    line.AfeProjectId AS afeProjectId,
                    line.SiteId AS siteId
                FROM prep.InvoiceLine AS line
                WHERE line.PackageId = p.PackageId
                ORDER BY line.LineNumber
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )
        ) AS lines,

        JSON_QUERY
        (
            (
                SELECT
                    p.BusinessUnit AS businessUnit,
                    p.ProjectStatus AS projectStatus,
                    p.ProjectType AS projectType,
                    p.BundlingInScope AS bundlingInScope
                FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
            )
        ) AS context,

        JSON_QUERY
        (
            (
                SELECT
                    p.MissingPo AS missingPo,
                    p.MissingProject AS missingProject,
                    p.MissingSite AS missingSite,
                    JSON_QUERY
                    (
                        CONCAT
                        (
                            '[',
                            COALESCE
                            (
                                (
                                    SELECT STRING_AGG
                                    (
                                        CAST
                                        (
                                            CONCAT
                                            (
                                                '"',
                                                STRING_ESCAPE(
                                                    warning.WarningText,
                                                    'json'
                                                ),
                                                '"'
                                            )
                                            AS nvarchar(max)
                                        ),
                                        ','
                                    ) WITHIN GROUP
                                    (
                                        ORDER BY warning.WarningOrdinal
                                    )
                                    FROM prep.InvoiceDataQualityWarning AS warning
                                    WHERE warning.PackageId = p.PackageId
                                ),
                                ''
                            ),
                            ']'
                        )
                    ) AS warnings
                FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
            )
        ) AS dataQuality,

        JSON_QUERY
        (
            (
                SELECT
                    p.SourcePackageName AS packageName,
                    p.SourceSha256 AS sourceSha256
                FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
            )
        ) AS source,

        CONCAT
        (
            CONVERT
            (
                varchar(23),
                CAST
                (
                    SWITCHOFFSET(p.CreatedAtUtc, '+00:00')
                    AS datetime2(3)
                ),
                126
            ),
            'Z'
        ) AS createdAtUtc

    FROM prep.InvoicePackage AS p
    WHERE p.PackageStatus = 'READY'
      AND EXISTS
      (
          SELECT 1
          FROM prep.InvoiceLine AS required_line
          WHERE required_line.PackageId = p.PackageId
      );
GO
