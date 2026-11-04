SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @Suffix nvarchar(36) = CONVERT(nvarchar(36), NEWID());
    DECLARE @Invoice nvarchar(128) = N'model-test-' + @Suffix;
    DECLARE @Project nvarchar(128) = N'model-project-' + @Suffix;
    DECLARE @Site nvarchar(128) = N'model-site-' + @Suffix;
    DECLARE @PO nvarchar(128) = N'model-po-' + @Suffix;
    DECLARE @CaseID bigint, @ThresholdID int, @PartID bigint;

    INSERT INTO prep.Project
        (AFEProjectID, ProjectDescription, ProjectTotalBudget, ProjectStatus, ProjectType, CountryRegion, BundlingInScope)
    VALUES (@Project, N'ROLLBACK TEST ONLY', 1000, 'OPEN', 'TEST', N'TEST', 1);
    INSERT INTO prep.Site (SiteID, CountryRegion, IsLeased)
    VALUES (@Site, N'TEST', NULL);
    INSERT INTO prep.PurchaseOrder (PONumber, AFEProjectID)
    VALUES (@PO, @Project);
    INSERT INTO prep.PurchaseOrderLine
        (PONumber, POLineNumber, PODescription, AFEProjectID, SiteID, POTaskCode, BusinessUnit, VendorID)
    VALUES (@PO, 1, N'ROLLBACK TEST ONLY', @Project, @Site, N'TEST', N'TEST', N'TEST');
    INSERT INTO prep.Invoice
        (InvoiceID, InvoiceNumber, InvoiceDate, VendorID, VendorName, InvoiceCurrency,
         InvoiceTotal, PONumber, CountryRegion, SourceSystem, HasLineItems, RunID, LoadedAtUtc)
    VALUES (@Invoice, @Invoice, '20261104', N'TEST', N'TEST', 'USD', 1000,
        @PO, N'TEST', N'TEST', 1, @Invoice, SYSUTCDATETIME());
    INSERT INTO prep.CodingDistribution
        (ApprovedCodingDistributionID, InvoiceID, CodedTaskCode, CodedTaskDescription,
         DistributionAmount, ApproverID, ApprovalDate)
    VALUES (@Invoice, @Invoice, N'TEST', N'ROLLBACK TEST ONLY', 1000, N'TEST', '20261104');
    INSERT INTO ref.CapitalizationThreshold
        (GovernedThreshold, ThresholdType, CountryRegion, Currency, CapitalizationPolicyReference, EffectiveFrom)
    VALUES (1000, 'TEST', N'TEST', 'USD', N'ROLLBACK TEST ONLY - NOT APPROVED POLICY', '20261104');
    SET @ThresholdID = SCOPE_IDENTITY();
    INSERT INTO ref.PartMaster
        (VendorID, PartNumber, Version, ItemDescription, Source, IsCurrent)
    VALUES (@Invoice, N'TEST', 1, N'ROLLBACK TEST ONLY', N'TEST', 1);
    SET @PartID = SCOPE_IDENTITY();
    INSERT INTO ref.NeverCapitalizeException (ItemCategory, Keyword, PolicyReference, Active)
    VALUES (N'TEST', N'TEST', N'ROLLBACK TEST ONLY', 0);
    INSERT INTO ref.RepairMaintenanceIndicator (Keyword, Category, SignalStrength, PolicyReference)
    VALUES (N'TEST', 'TEST', 0.5, N'ROLLBACK TEST ONLY');
    INSERT INTO review.ReviewCase
        (InvoiceID, CaseStatus, CreatedFromEventID, CreatedAtUtc, UpdatedAtUtc)
    VALUES (@Invoice, 'TEST', @Invoice, SYSUTCDATETIME(), SYSUTCDATETIME());
    SET @CaseID = SCOPE_IDENTITY();
    INSERT INTO review.ApprovedCoding
        (ReviewCaseID, InvoiceID, TaskCode, DistributionAmount, DecisionVersion)
    VALUES (@CaseID, @Invoice, N'TEST', 1000, 1);
    INSERT INTO review.POPAHandoff (ReviewCaseID, HandoffMethod, HandoffStatus, UpdatedAtUtc)
    VALUES (@CaseID, 'TEST', 'TEST', SYSUTCDATETIME());
    INSERT INTO ops.Outbox
        (EventType, SchemaVersion, InvoiceID, PayloadJson, OwnerComponent, CreatedAtUtc, IdempotencyKey)
    VALUES ('TEST', '1.0', @Invoice, N'{"invoiceId":"rollback-test-only"}', 'TEST', SYSUTCDATETIME(), @Invoice);

    IF NOT EXISTS (SELECT 1 FROM review.ReviewCase WHERE ReviewCaseID = @CaseID AND RowVersion IS NOT NULL)
        THROW 51130, 'Review case rowversion was not generated.', 1;
    IF NOT EXISTS (SELECT 1 FROM ref.CapitalizationThreshold WHERE ThresholdID = @ThresholdID)
        OR NOT EXISTS (SELECT 1 FROM ref.PartMaster WHERE PartMasterID = @PartID)
        THROW 51131, 'Reference identity keys were not generated.', 1;

    ROLLBACK TRANSACTION;
    PRINT 'Model parent/reference/review/outbox insertion checks passed; all test rows rolled back.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
