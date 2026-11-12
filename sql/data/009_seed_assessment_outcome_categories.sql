SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Slide 1 labels/guidance; DecisionCriteria are proposed operational interpretations.
DECLARE @ApplyChanges bit=0;
IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51360, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @ApplyChanges IS NULL
    THROW 51361, 'ApplyChanges must explicitly be 0 or 1.', 1;
IF OBJECT_ID(N'ref.AssessmentOutcomeCategory', N'U') IS NULL
    THROW 51362, 'Apply migration 018 before importing assessment categories.', 1;

DECLARE @Proposed TABLE (
    CategoryCode nvarchar(64) NOT NULL PRIMARY KEY, CategoryName nvarchar(256) NOT NULL,
    DecisionCriteria nvarchar(2000) NOT NULL, ReviewConsiderations nvarchar(2000) NOT NULL,
    SuggestedAction nvarchar(2000) NOT NULL, EnabledForDemo bit NOT NULL
);
INSERT INTO @Proposed VALUES
    (N'CAPEX', N'CAPEX',
     N'Qualifying capitalizable expenditure under the applicable policy and threshold. Missing or unapproved required facts remain review items; amount alone is insufficient.',
     N'No action needed', N'No action needed', 1),
    (N'NUVOLO_RM', N'Nuvolo R&M',
     N'Reliable Nuvolo provenance and repairs/maintenance context are required. Do not infer Nuvolo solely from repair wording.',
     N'Higher risk of misclassification because more judgment is needed.', N'Review Required', 1),
    (N'OPEX_BELOW_THRESHOLD', N'OPEX below threshold',
     N'Capitalizable by nature but below the applicable threshold, with relevant project/exception considerations evaluated. Deferred bundling is not evidence that no bundling potential exists.',
     N'No potential bundling found.',
     N'Reclassification to OPEX suggested before end of project.', 1),
    (N'OPEX_BY_NATURE', N'OPEX by nature',
     N'Expense by nature only after contextual policy evaluation. Qualifying replacements, major inspections and directly attributable asset costs require separate assessment.',
     N'Repairs, project-related travel expenses and tank inspections (based on key words) (MAP related) / NO bundling analysis',
     N'Reclassification to OPEX suggested before end of project.', 1),
    (N'CAPEX_POTENTIAL_BUNDLING', N'CAPEX potential bundling',
     N'Supported common task/asset class, site and project with potential qualifying combined expenditure. Disabled for the current demo.',
     N'No action needed as same task, same site and same project are suggesting future invoices may bring this capex above threshold by the end of the project.',
     N'No action needed until project closes', 0),
    (N'CAPEX_MISSING_SITE', N'CAPEX: site information missing',
     N'A supported capitalizable recommendation has missing site information. Missing site alone neither establishes capitalizable nature nor proves asset attribution.',
     N'Site information missing. to speed up the capitalization time.',
     N'Information to be obtained from project team before end of project', 1),
    (N'ARO_TAG', N'ARO tag',
     N'Supported potential retirement/removal/closure evidence warrants ARO review. A tag alone does not establish a liability, final OPEX treatment or an automatic GL posting.',
     N'Flag tank removal invoices by keywords so the ARO agent can retrieve them for liability calculation / NO bundling analysis',
     N'Reclassification to OPEX (ARO GL) before end of project.', 1);

DECLARE @CurrentRows bigint, @InsertedRows bigint;
BEGIN TRY
    BEGIN TRANSACTION;
    SELECT @CurrentRows=COUNT_BIG(*) FROM ref.AssessmentOutcomeCategory WITH (TABLOCKX, HOLDLOCK);
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @CurrentRows AS CurrentTableRows,
            (SELECT COUNT_BIG(*) FROM @Proposed) AS ProposedRows;
        SELECT * FROM @Proposed ORDER BY CategoryCode;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    IF EXISTS (
        SELECT CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo
        FROM ref.AssessmentOutcomeCategory
        EXCEPT
        SELECT CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo
        FROM @Proposed
    )
        THROW 51363, 'Existing category definitions conflict with the proposed catalog. Review before importing.', 1;
    INSERT INTO ref.AssessmentOutcomeCategory
        (CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo)
    SELECT CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo
    FROM @Proposed AS proposed
    WHERE NOT EXISTS (SELECT 1 FROM ref.AssessmentOutcomeCategory AS existing
        WHERE existing.CategoryCode=proposed.CategoryCode);
    SET @InsertedRows=@@ROWCOUNT;
    IF (SELECT COUNT_BIG(*) FROM ref.AssessmentOutcomeCategory)<>(SELECT COUNT_BIG(*) FROM @Proposed)
        OR EXISTS (
            SELECT CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo
            FROM @Proposed
            EXCEPT
            SELECT CategoryCode, CategoryName, DecisionCriteria, ReviewConsiderations, SuggestedAction, EnabledForDemo
            FROM ref.AssessmentOutcomeCategory
        )
        THROW 51364, 'Stored assessment categories do not reconcile.', 1;
    COMMIT TRANSACTION;
    SELECT 'ASSESSMENT_CATEGORIES_LOADED' AS Result,
        @InsertedRows AS InsertedRows, (SELECT COUNT_BIG(*) FROM @Proposed) AS TotalCategories,
        (SELECT COUNT_BIG(*) FROM @Proposed WHERE EnabledForDemo=1) AS EnabledForDemo;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
