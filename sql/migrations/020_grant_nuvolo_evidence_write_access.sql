-- Permission-only prerequisite for source-backed Nuvolo handling; no new tables.
-- Run as the approved database administrator, not the application identity.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51620, 'Create the approved runtime Entra contained user first.', 1;
IF COL_LENGTH(N'prep.Invoice', N'NuvoloWorkOrderNo') IS NULL
    OR COL_LENGTH(N'capex.Assessment', N'NuvoloRiskFlag') IS NULL
    THROW 51621, 'Apply migration 009 before granting Nuvolo evidence access.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    GRANT SELECT ON OBJECT::prep.Invoice (InvoiceID, NuvoloWorkOrderNo) TO [cict-ai-cus-capex-id];
    GRANT UPDATE ON OBJECT::prep.Invoice (NuvoloWorkOrderNo) TO [cict-ai-cus-capex-id];
    GRANT UPDATE ON OBJECT::capex.Assessment (NuvoloRiskFlag) TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT 'NUVOLO_EVIDENCE_WRITE_ACCESS_GRANTED' AS GrantStatus;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
