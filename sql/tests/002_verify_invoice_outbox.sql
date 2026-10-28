SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @PackageId nvarchar(128) =
        N'outbox-verification-package';
    DECLARE @EventId nvarchar(128) =
        N'evt-outbox-verification';
    DECLARE @Payload nvarchar(max) =
        N'{
            "eventId":"evt-outbox-verification",
            "eventType":"InvoiceReady",
            "schemaVersion":"1.0",
            "invoiceId":"outbox-verification-invoice",
            "packageId":"outbox-verification-package",
            "runId":"outbox-verification-run",
            "packageUri":"urn:circlek:capex:invoice-package:outbox-verification-package",
            "occurredAtUtc":"2026-10-19T19:00:00Z",
            "correlationId":"corr-outbox-verification"
        }';

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
        N'outbox-verification-run',
        N'outbox-verification-invoice',
        N'OUTBOX-001',
        '2026-10-19',
        NULL,
        N'Outbox Verification Vendor',
        'USD',
        10.00,
        NULL,
        N'North America',
        N'ABBYY',
        NULL,
        NULL,
        NULL,
        0,
        1,
        1,
        1,
        N'outbox-verification.zip',
        REPLICATE('b', 64),
        '2026-10-19T19:00:00+00:00',
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
        N'outbox-verification-invoice:1',
        1,
        N'Outbox verification line',
        NULL,
        1.00,
        10.00,
        10.00,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL
    );

    INSERT INTO prep.InvoiceOutbox
    (
        EventId,
        PackageId,
        EventType,
        EventVersion,
        CorrelationId,
        Payload,
        OutboxStatus
    )
    VALUES
    (
        @EventId,
        @PackageId,
        N'InvoiceReady',
        '1.0',
        N'corr-outbox-verification',
        @Payload,
        'PENDING'
    );

    IF EXISTS
    (
        SELECT 1
        FROM pub.v1_InvoicePackage
        WHERE packageId = @PackageId
    )
        THROW 51030,
            'PREPARING package was published.',
            1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM prep.InvoiceOutbox
        WHERE EventId = @EventId
          AND PackageId = @PackageId
          AND OutboxStatus = 'PENDING'
          AND JSON_VALUE(Payload, '$.eventType')
              = 'InvoiceReady'
          AND JSON_VALUE(Payload, '$.packageId')
              = @PackageId
    )
        THROW 51031,
            'InvoiceReady outbox record is invalid.',
            1;

    UPDATE prep.InvoicePackage
    SET
        PackageStatus = 'READY',
        UpdatedAtUtc = SYSUTCDATETIME()
    WHERE PackageId = @PackageId
      AND PackageStatus = 'PREPARING';

    IF @@ROWCOUNT <> 1
        THROW 51032,
            'Package did not transition to READY.',
            1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM pub.v1_InvoicePackage
        WHERE packageId = @PackageId
    )
        THROW 51033,
            'READY package was not published.',
            1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM prep.InvoiceOutbox
        WHERE PackageId = @PackageId
          AND OutboxStatus = 'PENDING'
    )
        THROW 51034,
            'Outbox record was not retained.',
            1;

    ROLLBACK TRANSACTION;

    PRINT 'Invoice outbox verification passed.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
