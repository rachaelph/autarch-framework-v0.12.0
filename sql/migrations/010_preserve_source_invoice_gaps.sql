SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'prep.Invoice', N'U') IS NULL
    OR OBJECT_ID(N'prep.InvoicePackage', N'U') IS NULL
    OR OBJECT_ID(N'prep.InvoiceLine', N'U') IS NULL
    THROW 51400, 'Apply migrations 001 through 009 before preserving source invoice gaps.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    -- Missing source facts must not become invented dates, vendors, or zero prices.
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'prep.Invoice')
        AND name = N'InvoiceDate' AND is_nullable = 0)
        ALTER TABLE prep.Invoice ALTER COLUMN InvoiceDate date NULL;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'prep.Invoice')
        AND name = N'VendorID' AND is_nullable = 0)
    BEGIN
        ALTER TABLE prep.Invoice DROP CONSTRAINT UQ_DM_Invoice_Vendor_Number;
        ALTER TABLE prep.Invoice ALTER COLUMN VendorID nvarchar(128) NULL;
        ALTER TABLE prep.Invoice ADD CONSTRAINT UQ_DM_Invoice_Vendor_Number
            UNIQUE (VendorID, InvoiceNumber);
    END;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'prep.InvoiceLine')
        AND name = N'Quantity' AND is_nullable = 0)
    BEGIN
        ALTER TABLE prep.InvoiceLine DROP CONSTRAINT CK_InvoiceLine_Quantity;
        ALTER TABLE prep.InvoiceLine ALTER COLUMN Quantity decimal(19,4) NULL;
        ALTER TABLE prep.InvoiceLine WITH CHECK ADD CONSTRAINT CK_InvoiceLine_Quantity
            CHECK (Quantity >= 0);
    END;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'prep.InvoiceLine')
        AND name = N'UnitCost' AND is_nullable = 0)
        ALTER TABLE prep.InvoiceLine ALTER COLUMN UnitCost decimal(19,4) NULL;

    IF COL_LENGTH(N'prep.InvoicePackage', N'SourceMetadataJson') IS NULL
        ALTER TABLE prep.InvoicePackage ADD SourceMetadataJson nvarchar(max) NULL
            CONSTRAINT CK_InvoicePackage_SourceMetadataJson
            CHECK (SourceMetadataJson IS NULL OR ISJSON(SourceMetadataJson) = 1);
    IF COL_LENGTH(N'prep.InvoiceLine', N'SourceLineJson') IS NULL
        ALTER TABLE prep.InvoiceLine ADD SourceLineJson nvarchar(max) NULL
            CONSTRAINT CK_InvoiceLine_SourceLineJson
            CHECK (SourceLineJson IS NULL OR ISJSON(SourceLineJson) = 1);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
