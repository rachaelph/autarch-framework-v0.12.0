-- Preview first. User-confirmed policy applicability starts 2026-04-27.
-- No exchange rates are invented or loaded here. No historical results are modified.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit=1;
DECLARE @ExpectedActiveRows bigint=3;
DECLARE @EffectiveFrom date='2026-04-27';
DECLARE @PolicyVersion nvarchar(128)=N'REPORTING_MANUAL_APRIL_2026_USD_V1';
DECLARE @Reference nvarchar(2000)=N'Reporting manual - April 2026 - Final, Property and Equipment, section 4; printed page 45 / PDF page 47. USD 1000 expenditure threshold at budget rate; below-threshold exception requires USD 300 minimum per site AND USD 30000 aggregate project expenditure across divisions/BUs. Printed page 46 / PDF page 48 contains additional exception guidance requiring judgment. Assessment unit and budget-rate applicability require Finance confirmation.';

IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51382, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF COL_LENGTH(N'ref.CapitalizationThreshold',N'MinimumSiteAmount') IS NULL
    THROW 51383, 'Apply migration 019 first.', 1;
IF @ApplyChanges IS NULL OR (@ApplyChanges=1 AND
    (@ExpectedActiveRows IS NULL OR @ExpectedActiveRows<0 OR @EffectiveFrom IS NULL))
    THROW 51384, 'Preview, confirm ExpectedActiveRows and supply the policy-owner-confirmed EffectiveFrom date.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @ActiveRows bigint;
    SELECT @ActiveRows=COUNT_BIG(*) FROM ref.CapitalizationThreshold WITH (TABLOCKX,HOLDLOCK)
        WHERE IsActive=1;
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @ActiveRows AS CurrentActiveRows,
            @EffectiveFrom AS ProposedEffectiveFrom;
        SELECT 'RETAIN_HISTORY_DEACTIVATE_SYNTHETIC' AS ProposedAction, *
            FROM ref.CapitalizationThreshold WHERE IsActive=1;
        SELECT @PolicyVersion AS PolicyVersion, 1000 AS ThresholdUsd,
            300 AS MinimumPerSiteUsd, 30000 AS MinimumProjectAggregateUsd,
            @Reference AS PolicyReference;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    IF @ActiveRows<>@ExpectedActiveRows
        THROW 51385, 'Active threshold count changed; preview again.', 1;
    IF EXISTS (SELECT 1 FROM ref.CapitalizationThreshold WHERE IsActive=1
        AND (ThresholdType<>'FRIDAY_DEMO_UNIT_COST_GTE'
             OR CapitalizationPolicyReference NOT LIKE N'%UNAPPROVED%'))
        THROW 51386, 'A non-synthetic active policy exists. Obtain a reviewed policy transition instead of replacing it.', 1;
    IF EXISTS (SELECT 1 FROM ref.CapitalizationThreshold WHERE PolicyVersion=@PolicyVersion)
        THROW 51387, 'This policy version already exists; inspect it rather than creating duplicate effective rules.', 1;
    UPDATE ref.CapitalizationThreshold SET IsActive=0 WHERE IsActive=1;
    INSERT INTO ref.CapitalizationThreshold
        (GovernedThreshold,ThresholdType,CountryRegion,Currency,ProjectBudgetMin,
         CapitalizationPolicyReference,IFRSReference,EffectiveFrom,EffectiveTo,
         PolicyVersion,MinimumSiteAmount,IsActive)
    VALUES (1000,'USD_BUDGET_EXPENDITURE',N'Global','USD',30000,
        @Reference,N'Property and equipment recognition criteria remain required.',
        @EffectiveFrom,NULL,@PolicyVersion,300,1);
    COMMIT TRANSACTION;
    SELECT 'USD_REPORTING_POLICY_ACTIVATED' AS Result,
        @PolicyVersion AS PolicyVersion, @EffectiveFrom AS EffectiveFrom;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
