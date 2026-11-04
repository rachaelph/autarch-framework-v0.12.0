SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51530, 'Create the approved runtime Entra contained user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    GRANT SELECT ON OBJECT::prep.Invoice TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.InvoicePackage TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.InvoiceLine TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.Project TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.Site TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.PurchaseOrder TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::prep.PurchaseOrderLine TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::ref.PartMaster TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::ref.CapitalizationThreshold TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::ref.NeverCapitalizeException TO [cict-ai-cus-capex-id];
    GRANT SELECT ON OBJECT::ref.RepairMaintenanceIndicator TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT 'SQL_DEMO_INPUT_READ_ACCESS_GRANTED' AS GrantStatus;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
