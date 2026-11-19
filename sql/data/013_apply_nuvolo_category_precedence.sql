-- Confirmed November 17, 2026: FWKD in a PO means Nuvolo, including when ARO applies.
-- Data-only update to the existing seven-category catalog; preview is the default.
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @ApplyChanges bit=0;
IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51630, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @ApplyChanges IS NULL
    THROW 51631, 'ApplyChanges must explicitly be 0 or 1.', 1;
IF OBJECT_ID(N'ref.AssessmentOutcomeCategory', N'U') IS NULL
    THROW 51632, 'Apply migration 018 and seed 009 before updating category criteria.', 1;

DECLARE @Expected TABLE (CategoryCode nvarchar(64) NOT NULL PRIMARY KEY);
INSERT INTO @Expected VALUES
    (N'CAPEX'), (N'NUVOLO_RM'), (N'OPEX_BELOW_THRESHOLD'), (N'OPEX_BY_NATURE'),
    (N'CAPEX_POTENTIAL_BUNDLING'), (N'CAPEX_MISSING_SITE'), (N'ARO_TAG');

DECLARE @Changes TABLE (
    CategoryCode nvarchar(64) NOT NULL PRIMARY KEY,
    PreviousCriteria nvarchar(2000) NOT NULL,
    DecisionCriteria nvarchar(2000) NOT NULL
);
INSERT INTO @Changes VALUES
    (N'NUVOLO_RM',
     N'Reliable Nuvolo provenance and repairs/maintenance context are required. Do not infer Nuvolo solely from repair wording.',
     N'FWKD anywhere in an original purchase-order field, including Customer PO, assigns Nuvolo R&M as the sole primary category. Nuvolo wins over ARO and other primary categories without requiring separate repairs/maintenance keywords. Malformed or conflicting numeric work orders still require analyst review; never guess a number. This category does not change the accounting recommendation.'),
    (N'ARO_TAG',
     N'Supported potential retirement/removal/closure evidence warrants ARO review. A tag alone does not establish a liability, final OPEX treatment or an automatic GL posting.',
     N'Supported ARO keyword evidence warrants ARO review. When FWKD also appears in an original purchase-order field, Nuvolo R&M wins as the primary category; retain ARO evidence for review. A tag alone does not establish a liability, final OPEX treatment or an automatic GL posting.');

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @CurrentRows bigint, @UpdatedRows int;
    SELECT @CurrentRows=COUNT_BIG(*) FROM ref.AssessmentOutcomeCategory WITH (TABLOCKX, HOLDLOCK);
    IF @CurrentRows<>7
        OR EXISTS (
            SELECT CategoryCode FROM ref.AssessmentOutcomeCategory
            EXCEPT SELECT CategoryCode FROM @Expected
        )
        OR EXISTS (
            SELECT CategoryCode FROM @Expected
            EXCEPT SELECT CategoryCode FROM ref.AssessmentOutcomeCategory
        )
        THROW 51633, 'Expected exactly the seven confirmed categories; review the catalog before applying.', 1;

    SELECT stored.CategoryCode, stored.DecisionCriteria AS CurrentCriteria,
        proposed.DecisionCriteria AS ProposedCriteria
    FROM ref.AssessmentOutcomeCategory AS stored
    JOIN @Changes AS proposed ON proposed.CategoryCode=stored.CategoryCode
    ORDER BY stored.CategoryCode;
    IF @ApplyChanges=0
    BEGIN
        ROLLBACK TRANSACTION;
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result;
        RETURN;
    END;
    IF EXISTS (
        SELECT 1 FROM ref.AssessmentOutcomeCategory AS stored
        JOIN @Changes AS proposed ON proposed.CategoryCode=stored.CategoryCode
        WHERE CONVERT(varbinary(max), stored.DecisionCriteria) NOT IN (
            CONVERT(varbinary(max), proposed.PreviousCriteria),
            CONVERT(varbinary(max), proposed.DecisionCriteria)
        )
    )
        THROW 51634, 'Unrecognized existing criteria; review them instead of overwriting other policy changes.', 1;
    IF NOT EXISTS (
        SELECT 1 FROM ref.AssessmentOutcomeCategory
        WHERE CategoryCode=N'NUVOLO_RM' AND EnabledForDemo=1
    )
        THROW 51635, 'Nuvolo must be enabled; review catalog enablement separately.', 1;

    UPDATE stored SET DecisionCriteria=proposed.DecisionCriteria
    FROM ref.AssessmentOutcomeCategory AS stored
    JOIN @Changes AS proposed ON proposed.CategoryCode=stored.CategoryCode
    WHERE CONVERT(varbinary(max), stored.DecisionCriteria)
        <>CONVERT(varbinary(max), proposed.DecisionCriteria);
    SET @UpdatedRows=@@ROWCOUNT;
    IF EXISTS (
        SELECT CategoryCode, CONVERT(varbinary(max), DecisionCriteria) FROM @Changes
        EXCEPT
        SELECT CategoryCode, CONVERT(varbinary(max), DecisionCriteria) FROM ref.AssessmentOutcomeCategory
    )
        THROW 51636, 'Updated category criteria did not reconcile; nothing was committed.', 1;
    COMMIT TRANSACTION;
    SELECT 'NUVOLO_CATEGORY_PRECEDENCE_APPLIED' AS Result, @UpdatedRows AS UpdatedRows;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
