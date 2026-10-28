SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @PackageId nvarchar(128) =
        N'sql-verification-package';

    INSERT INTO prep.InvoicePackage
    (
        PackageId,
        SchemaVersion,
        RunId,
        InvoiceId,
        InvoiceNumber,
        InvoiceDate,
        VendorId,
        VendorName,
        Currency,
        InvoiceTotal,
        PoNumber,
        CountryRegion,
        SourceSystem,
        BusinessUnit,
        ProjectStatus,
        ProjectType,
        BundlingInScope,
        MissingPo,
        MissingProject,
        MissingSite,
        SourcePackageName,
        SourceSha256,
        CreatedAtUtc,
        PackageStatus
    )
    VALUES
    (
        @PackageId,
        '1.0',
        N'sql-verification-run',
        N'sql-verification-invoice',
        N'TEST-001',
        '2026-10-19',
        NULL,
        N'Contract Test Vendor',
        'USD',
        30.00,
        NULL,
        N'North America',
        N'ABBYY',
        N'US',
        NULL,
        NULL,
        0,
        1,
        1,
        1,
        N'test-package.zip',
        REPLICATE('a', 64),
        '2026-10-19T13:00:00+00:00',
        'PREPARING'
    );

    INSERT INTO prep.InvoiceLine
    (
        PackageId,
        InvoiceLineId,
        LineNumber,
        Description,
        PartNumber,
        Quantity,
        UnitCost,
        LineAmount,
        PoLineNumber,
        PoDescription,
        PoTaskCode,
        AfeProjectId,
        SiteId
    )
    VALUES
    (
        @PackageId,
        N'sql-verification-invoice:1',
        1,
        N'First verification line',
        NULL,
        1.00,
        10.00,
        10.00,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL
    ),
    (
        @PackageId,
        N'sql-verification-invoice:2',
        2,
        N'Second verification line',
        NULL,
        2.00,
        10.00,
        20.00,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL
    );

    INSERT INTO prep.InvoiceDataQualityWarning
    (
        PackageId,
        WarningOrdinal,
        WarningText
    )
    VALUES
    (
        @PackageId,
        1,
        N'Purchase-order context was not supplied.'
    ),
    (
        @PackageId,
        2,
        N'Site context was not supplied.'
    );

    IF EXISTS
    (
        SELECT 1
        FROM pub.v1_InvoicePackage
        WHERE packageId = @PackageId
    )
        THROW 51000,
            'PREPARING packages must not be published.',
            1;

    UPDATE prep.InvoicePackage
    SET
        PackageStatus = 'READY',
        UpdatedAtUtc = SYSUTCDATETIME()
    WHERE PackageId = @PackageId;

    DECLARE
        @SchemaVersion varchar(16),
        @Invoice nvarchar(max),
        @Lines nvarchar(max),
        @Context nvarchar(max),
        @DataQuality nvarchar(max),
        @Source nvarchar(max),
        @CreatedAtUtc varchar(24);

    SELECT
        @SchemaVersion = schemaVersion,
        @Invoice = invoice,
        @Lines = lines,
        @Context = context,
        @DataQuality = dataQuality,
        @Source = source,
        @CreatedAtUtc = createdAtUtc
    FROM pub.v1_InvoicePackage
    WHERE packageId = @PackageId;

    IF @SchemaVersion <> '1.0'
        THROW 51001, 'Unexpected schema version.', 1;

    IF JSON_VALUE(@Invoice, '$.invoiceNumber') <> 'TEST-001'
        THROW 51002, 'Invoice JSON is invalid.', 1;

    IF
    (
        SELECT COUNT(*)
        FROM OPENJSON(@Lines)
    ) <> 2
        THROW 51003, 'Published line count is incorrect.', 1;

    IF JSON_VALUE(@Lines, '$[0].lineNumber') <> '1'
        THROW 51004, 'Published line ordering is incorrect.', 1;

    IF JSON_VALUE(@Context, '$.businessUnit') <> 'US'
        THROW 51005, 'Context JSON is invalid.', 1;

    IF
    (
        SELECT COUNT(*)
        FROM OPENJSON(
            JSON_QUERY(@DataQuality, '$.warnings')
        )
    ) <> 2
        THROW 51006, 'Published warning count is incorrect.', 1;

    IF JSON_VALUE(@Source, '$.sourceSha256') <> REPLICATE('a', 64)
        THROW 51007, 'Source provenance is invalid.', 1;

    IF @CreatedAtUtc <> '2026-10-19T13:00:00.000Z'
        THROW 51008, 'UTC timestamp formatting is invalid.', 1;

    SELECT
        schemaVersion,
        packageId,
        runId,
        JSON_QUERY(invoice) AS invoice,
        JSON_QUERY(lines) AS lines,
        JSON_QUERY(context) AS context,
        JSON_QUERY(dataQuality) AS dataQuality,
        JSON_QUERY(source) AS source,
        createdAtUtc
    FROM pub.v1_InvoicePackage
    WHERE packageId = @PackageId
    FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;

    ROLLBACK TRANSACTION;

    PRINT 'Invoice Operational Store verification passed.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
