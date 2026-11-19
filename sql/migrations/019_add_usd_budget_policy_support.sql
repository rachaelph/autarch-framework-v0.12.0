-- DBA handoff: additive schema only; no policy activation, rates or historical updates.
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51380, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF OBJECT_ID(N'ref.CapitalizationThreshold', N'U') IS NULL
    OR OBJECT_ID(N'capex.Assessment', N'U') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51381, 'Apply migrations through 018 with the existing runtime user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF COL_LENGTH(N'ref.CapitalizationThreshold', N'PolicyVersion') IS NULL
        ALTER TABLE ref.CapitalizationThreshold ADD PolicyVersion nvarchar(128) NULL;
    IF COL_LENGTH(N'ref.CapitalizationThreshold', N'MinimumSiteAmount') IS NULL
        ALTER TABLE ref.CapitalizationThreshold ADD MinimumSiteAmount decimal(19,4) NULL
            CONSTRAINT CK_Threshold_MinimumSiteAmount CHECK (MinimumSiteAmount>=0);
    IF COL_LENGTH(N'ref.CapitalizationThreshold', N'IsActive') IS NULL
        ALTER TABLE ref.CapitalizationThreshold ADD IsActive bit NOT NULL
            CONSTRAINT DF_Threshold_IsActive DEFAULT (1) WITH VALUES;

    IF OBJECT_ID(N'ref.BudgetExchangeRate', N'U') IS NULL
        CREATE TABLE ref.BudgetExchangeRate (
            BudgetExchangeRateID int IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_BudgetExchangeRate PRIMARY KEY,
            SourceCurrency char(3) NOT NULL,
            TargetCurrency char(3) NOT NULL CONSTRAINT DF_BudgetRate_Target DEFAULT ('USD'),
            UsdPerSourceUnit decimal(28,12) NOT NULL,
            CountryRegion nvarchar(100) NOT NULL,
            BudgetPeriod nvarchar(128) NOT NULL,
            RateVersion nvarchar(128) NOT NULL,
            SourceReference nvarchar(2000) NOT NULL,
            EffectiveFrom date NOT NULL,
            EffectiveTo date NOT NULL,
            ApprovalStatus varchar(16) NOT NULL,
            ApprovedBy nvarchar(256) NULL,
            ApprovedAtUtc datetime2(3) NULL,
            IsActive bit NOT NULL CONSTRAINT DF_BudgetRate_IsActive DEFAULT (1),
            CONSTRAINT CK_BudgetRate_Currencies CHECK (
                SourceCurrency COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^A-Z]%'
                AND TargetCurrency='USD' AND SourceCurrency<>'USD'),
            CONSTRAINT CK_BudgetRate_Positive CHECK (UsdPerSourceUnit>0),
            CONSTRAINT CK_BudgetRate_Dates CHECK (EffectiveTo>=EffectiveFrom),
            CONSTRAINT CK_BudgetRate_Approval CHECK (
                ApprovalStatus IN ('PENDING','APPROVED','REJECTED')
                AND (ApprovalStatus<>'APPROVED' OR
                     (ApprovedBy IS NOT NULL AND ApprovedAtUtc IS NOT NULL))),
            CONSTRAINT UQ_BudgetRate_Version UNIQUE
                (SourceCurrency, CountryRegion, BudgetPeriod, RateVersion, EffectiveFrom)
        );

    -- Run-scoped lines avoid overwriting prep amounts or earlier FX/policy evidence.
    IF OBJECT_ID(N'capex.InvoiceLineAmount', N'U') IS NULL
        CREATE TABLE capex.InvoiceLineAmount (
            WorkflowRunId nvarchar(128) NOT NULL,
            PackageId nvarchar(128) NOT NULL,
            LineId nvarchar(256) NOT NULL,
            OriginalCurrency char(3) NOT NULL,
            OriginalUnitCost decimal(38,10) NULL,
            OriginalLineAmount decimal(38,10) NOT NULL,
            BudgetExchangeRateID int NULL,
            RateToUsd decimal(28,12) NULL,
            UnitCostUsd decimal(38,10) NULL,
            LineAmountUsd decimal(38,10) NULL,
            ConversionStatus varchar(16) NOT NULL,
            PolicyAsOfDate date NOT NULL,
            EvidenceJson nvarchar(max) NOT NULL,
            CONSTRAINT PK_InvoiceLineAmount PRIMARY KEY (WorkflowRunId, PackageId, LineId),
            CONSTRAINT FK_InvoiceLineAmount_Assessment FOREIGN KEY (WorkflowRunId, PackageId, LineId)
                REFERENCES capex.Assessment (WorkflowRunId, PackageId, LineId),
            CONSTRAINT FK_InvoiceLineAmount_Rate FOREIGN KEY (BudgetExchangeRateID)
                REFERENCES ref.BudgetExchangeRate (BudgetExchangeRateID),
            CONSTRAINT CK_InvoiceLineAmount_Json CHECK (ISJSON(EvidenceJson)=1),
            CONSTRAINT CK_InvoiceLineAmount_Status CHECK (
                (ConversionStatus='CONVERTED' AND RateToUsd IS NOT NULL AND RateToUsd>0
                    AND LineAmountUsd IS NOT NULL
                    AND ((OriginalUnitCost IS NULL AND UnitCostUsd IS NULL)
                         OR (OriginalUnitCost IS NOT NULL AND UnitCostUsd IS NOT NULL))
                    AND ((OriginalCurrency='USD' AND RateToUsd=1 AND BudgetExchangeRateID IS NULL)
                         OR (OriginalCurrency<>'USD' AND BudgetExchangeRateID IS NOT NULL)))
                OR (ConversionStatus='REVIEW' AND RateToUsd IS NULL AND BudgetExchangeRateID IS NULL
                    AND UnitCostUsd IS NULL AND LineAmountUsd IS NULL))
        );
    GRANT SELECT ON OBJECT::ref.BudgetExchangeRate TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::capex.InvoiceLineAmount TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT 'USD_BUDGET_POLICY_SCHEMA_READY' AS Result;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
