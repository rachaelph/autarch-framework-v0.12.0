-- User-supplied F27 FX Rate schedule and inclusive FY27 dates. Preview only.
-- Source amounts are multiplied by UsdPerSourceUnit. USD uses identity conversion.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit=1;
DECLARE @UsdPerSourceUnitConfirmed bit=1;
DECLARE @EffectiveFrom date='2026-04-27';
DECLARE @EffectiveTo date='2027-04-25';
DECLARE @ApprovalStatus varchar(16)='APPROVED';
DECLARE @ApprovedBy nvarchar(256)='Marie';
DECLARE @ApprovedAtUtc datetime2(3)=SYSUTCDATETIME();
DECLARE @CountryRegion nvarchar(100)=N'Global';
DECLARE @BudgetPeriod nvarchar(128)=N'FY27';
DECLARE @RateVersion nvarchar(128)=N'F27 FX Rate';
DECLARE @SourceReference nvarchar(2000)=N'User-provided FY27 fixed exchange-rate schedule and matching screenshot; version F27 FX Rate. User-provided FY27 dates: 2026-04-27 through 2027-04-25 inclusive. Confirm USD-per-source-unit direction, scope and Finance approval before approved use.';

IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51388, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF OBJECT_ID(N'ref.BudgetExchangeRate',N'U') IS NULL
    THROW 51389, 'Apply migration 019 before importing budget rates.', 1;
IF @ApplyChanges IS NULL OR @ApprovalStatus NOT IN ('PENDING','APPROVED')
    OR @ApprovalStatus IS NULL OR NULLIF(LTRIM(RTRIM(@CountryRegion)),N'') IS NULL
    OR NULLIF(LTRIM(RTRIM(@SourceReference)),N'') IS NULL
    THROW 51390, 'Supply explicit import mode, scope, source and PENDING or APPROVED status.', 1;
IF @ApplyChanges=1 AND (ISNULL(@UsdPerSourceUnitConfirmed,0)<>1
    OR @EffectiveFrom IS NULL OR @EffectiveTo IS NULL OR @EffectiveTo<@EffectiveFrom)
    THROW 51391, 'Confirm USD per one source-currency unit and supply Finance-confirmed inclusive effective dates.', 1;
IF @ApprovalStatus='APPROVED' AND (
    NULLIF(LTRIM(RTRIM(@ApprovedBy)),N'') IS NULL OR @ApprovedAtUtc IS NULL)
    THROW 51392, 'APPROVED rates require actual Finance approval identity and UTC timestamp.', 1;
IF @ApprovalStatus='PENDING' AND (@ApprovedBy IS NOT NULL OR @ApprovedAtUtc IS NOT NULL)
    THROW 51393, 'Pending rates must not claim approval provenance.', 1;

DECLARE @Rates TABLE (
    SourceCurrency char(3) NOT NULL PRIMARY KEY,
    UsdPerSourceUnit decimal(28,12) NOT NULL CHECK (UsdPerSourceUnit>0)
);
INSERT INTO @Rates (SourceCurrency,UsdPerSourceUnit) VALUES
    ('USD',1.0000),
    ('EUR',1.1662),
    ('CAD',0.7243),
    ('CHF',1.2408),
    ('DKK',0.1558),
    ('GBP',1.3463),
    ('HKD',0.1282),
    ('INR',0.0114),
    ('NOK',0.1003),
    ('PLN',0.2740),
    ('SEK',0.1063);

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @ExistingVersionRows bigint;
    SELECT @ExistingVersionRows=COUNT_BIG(*)
    FROM ref.BudgetExchangeRate WITH (TABLOCKX,HOLDLOCK)
    WHERE CountryRegion=@CountryRegion AND BudgetPeriod=@BudgetPeriod AND RateVersion=@RateVersion;
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @ExistingVersionRows AS ExistingVersionRows,
            @UsdPerSourceUnitConfirmed AS UsdPerSourceUnitConfirmed;
        SELECT SourceCurrency, 'USD' AS TargetCurrency, UsdPerSourceUnit,
            CASE WHEN SourceCurrency='USD' THEN 'IDENTITY_NO_ROW' ELSE 'INSERT_BUDGET_RATE' END AS ProposedAction,
            @CountryRegion AS CountryRegion, @BudgetPeriod AS BudgetPeriod, @RateVersion AS RateVersion,
            @SourceReference AS SourceReference, @EffectiveFrom AS EffectiveFrom, @EffectiveTo AS EffectiveTo,
            @ApprovalStatus AS ApprovalStatus, @ApprovedBy AS ApprovedBy, @ApprovedAtUtc AS ApprovedAtUtc
        FROM @Rates ORDER BY SourceCurrency;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    IF @ExistingVersionRows<>0
        THROW 51394, 'This scoped budget-rate version already exists. Inspect it; do not overwrite or duplicate it.', 1;
    IF @ApprovalStatus='APPROVED' AND EXISTS (
        SELECT 1 FROM ref.BudgetExchangeRate AS existing
        JOIN @Rates AS proposed ON proposed.SourceCurrency=existing.SourceCurrency
        WHERE existing.IsActive=1 AND existing.ApprovalStatus='APPROVED'
            AND (existing.CountryRegion=@CountryRegion OR existing.CountryRegion=N'Global'
                 OR @CountryRegion=N'Global')
            AND existing.EffectiveFrom<=@EffectiveTo AND existing.EffectiveTo>=@EffectiveFrom
    )
        THROW 51395, 'An active approved rate overlaps these dates and scope. Obtain a reviewed version transition.', 1;
    INSERT INTO ref.BudgetExchangeRate
        (SourceCurrency,TargetCurrency,UsdPerSourceUnit,CountryRegion,BudgetPeriod,RateVersion,
         SourceReference,EffectiveFrom,EffectiveTo,ApprovalStatus,ApprovedBy,ApprovedAtUtc,IsActive)
    SELECT SourceCurrency,'USD',UsdPerSourceUnit,@CountryRegion,@BudgetPeriod,@RateVersion,
        @SourceReference,@EffectiveFrom,@EffectiveTo,@ApprovalStatus,@ApprovedBy,@ApprovedAtUtc,1
    FROM @Rates WHERE SourceCurrency<>'USD';
    DECLARE @InsertedRows int=@@ROWCOUNT;
    IF @InsertedRows<>10
        THROW 51396, 'Expected exactly ten non-USD budget rates; rolling back.', 1;
    COMMIT TRANSACTION;
    SELECT 'F27_BUDGET_RATES_IMPORTED' AS Result, @InsertedRows AS ImportedRows,
        @RateVersion AS RateVersion, @ApprovalStatus AS ApprovalStatus;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
