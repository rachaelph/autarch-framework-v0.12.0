SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF SCHEMA_ID(N'prep') IS NULL
        EXEC(N'CREATE SCHEMA prep AUTHORIZATION dbo;');

    IF SCHEMA_ID(N'pub') IS NULL
        EXEC(N'CREATE SCHEMA pub AUTHORIZATION dbo;');

    IF OBJECT_ID(N'prep.InvoicePackage', N'U') IS NULL
    BEGIN
        CREATE TABLE prep.InvoicePackage
        (
            PackageId          nvarchar(128)  NOT NULL,
            SchemaVersion      varchar(16)    NOT NULL,
            RunId              nvarchar(128)  NOT NULL,

            InvoiceId          nvarchar(128)  NOT NULL,
            InvoiceNumber      nvarchar(128)  NOT NULL,
            InvoiceDate        date           NULL,
            VendorId           nvarchar(128)  NULL,
            VendorName         nvarchar(300)  NOT NULL,
            Currency           char(3)        NOT NULL,
            InvoiceTotal       decimal(19, 4) NOT NULL,
            PoNumber           nvarchar(128)  NULL,
            CountryRegion      nvarchar(100)  NOT NULL,
            SourceSystem       nvarchar(64)   NOT NULL,

            BusinessUnit       nvarchar(100)  NULL,
            ProjectStatus      nvarchar(100)  NULL,
            ProjectType        nvarchar(100)  NULL,
            BundlingInScope    bit            NOT NULL,

            MissingPo          bit            NOT NULL,
            MissingProject     bit            NOT NULL,
            MissingSite        bit            NOT NULL,

            SourcePackageName  nvarchar(1024) NOT NULL,
            SourceSha256       char(64)       NOT NULL,
            CreatedAtUtc       datetimeoffset(3) NOT NULL,

            PackageStatus      varchar(16)    NOT NULL
                CONSTRAINT DF_InvoicePackage_PackageStatus
                DEFAULT ('PREPARING'),

            InsertedAtUtc      datetimeoffset(3) NOT NULL
                CONSTRAINT DF_InvoicePackage_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            UpdatedAtUtc       datetimeoffset(3) NOT NULL
                CONSTRAINT DF_InvoicePackage_UpdatedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_InvoicePackage
                PRIMARY KEY (PackageId),

            CONSTRAINT UQ_InvoicePackage_Run_Invoice
                UNIQUE (RunId, InvoiceId),

            CONSTRAINT CK_InvoicePackage_SchemaVersion
                CHECK (SchemaVersion = '1.0'),

            CONSTRAINT CK_InvoicePackage_InvoiceTotal
                CHECK (InvoiceTotal >= 0),

            CONSTRAINT CK_InvoicePackage_Currency
                CHECK (
                    Currency COLLATE Latin1_General_100_BIN2
                    NOT LIKE '%[^A-Z]%'
                ),

            CONSTRAINT CK_InvoicePackage_SourceSha256
                CHECK (
                    LEN(SourceSha256) = 64
                    AND SourceSha256 COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^0-9a-f]%'
                ),

            CONSTRAINT CK_InvoicePackage_CreatedAtUtc
                CHECK (DATEPART(TZOFFSET, CreatedAtUtc) = 0),

            CONSTRAINT CK_InvoicePackage_Status
                CHECK (
                    PackageStatus IN (
                        'PREPARING',
                        'READY',
                        'FAILED'
                    )
                )
        );
    END;

    IF OBJECT_ID(N'prep.InvoiceLine', N'U') IS NULL
    BEGIN
        CREATE TABLE prep.InvoiceLine
        (
            PackageId          nvarchar(128)  NOT NULL,
            InvoiceLineId      nvarchar(256)  NOT NULL,
            LineNumber         int            NOT NULL,
            Description        nvarchar(2000) NOT NULL,
            PartNumber         nvarchar(256)  NULL,
            Quantity           decimal(19, 4) NOT NULL,
            UnitCost           decimal(19, 4) NOT NULL,
            LineAmount         decimal(19, 4) NOT NULL,
            PoLineNumber       int            NULL,
            PoDescription      nvarchar(2000) NULL,
            PoTaskCode         nvarchar(128)  NULL,
            AfeProjectId       nvarchar(128)  NULL,
            SiteId             nvarchar(128)  NULL,

            CONSTRAINT PK_InvoiceLine
                PRIMARY KEY (PackageId, InvoiceLineId),

            CONSTRAINT UQ_InvoiceLine_Package_LineNumber
                UNIQUE (PackageId, LineNumber),

            CONSTRAINT FK_InvoiceLine_InvoicePackage
                FOREIGN KEY (PackageId)
                REFERENCES prep.InvoicePackage (PackageId)
                ON DELETE CASCADE,

            CONSTRAINT CK_InvoiceLine_LineNumber
                CHECK (LineNumber >= 1),

            CONSTRAINT CK_InvoiceLine_Description
                CHECK (LEN(Description) >= 1),

            CONSTRAINT CK_InvoiceLine_Quantity
                CHECK (Quantity >= 0),

            CONSTRAINT CK_InvoiceLine_PoLineNumber
                CHECK (
                    PoLineNumber IS NULL
                    OR PoLineNumber >= 1
                )
        );
    END;

    IF OBJECT_ID(N'prep.InvoiceDataQualityWarning', N'U') IS NULL
    BEGIN
        CREATE TABLE prep.InvoiceDataQualityWarning
        (
            PackageId          nvarchar(128)  NOT NULL,
            WarningOrdinal     int            NOT NULL,
            WarningText        nvarchar(2000) NOT NULL,

            CONSTRAINT PK_InvoiceDataQualityWarning
                PRIMARY KEY (PackageId, WarningOrdinal),

            CONSTRAINT FK_InvoiceDataQualityWarning_InvoicePackage
                FOREIGN KEY (PackageId)
                REFERENCES prep.InvoicePackage (PackageId)
                ON DELETE CASCADE,

            CONSTRAINT CK_InvoiceDataQualityWarning_Ordinal
                CHECK (WarningOrdinal >= 1),

            CONSTRAINT CK_InvoiceDataQualityWarning_Text
                CHECK (LEN(WarningText) >= 1)
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'prep.InvoicePackage')
          AND name = N'IX_InvoicePackage_Status_CreatedAtUtc'
    )
    BEGIN
        CREATE INDEX IX_InvoicePackage_Status_CreatedAtUtc
            ON prep.InvoicePackage
            (
                PackageStatus,
                CreatedAtUtc
            )
            INCLUDE
            (
                InvoiceId,
                RunId
            );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
