SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Source: CAPEX_Microsoft_AI_POC_V3 nov 11.pptx, slide 3 only.
DECLARE @ApplyChanges bit = 0;

IF DB_NAME() <> N'cict-ai-cus-sqldb' OR @@TRANCOUNT <> 0
    THROW 51342, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @ApplyChanges IS NULL
    THROW 51343, 'ApplyChanges must explicitly be 0 or 1.', 1;
IF OBJECT_ID(N'ref.AROIndicator', N'U') IS NULL
    THROW 51344, 'Apply migration 017 before importing ARO indicators.', 1;

DECLARE @Groups TABLE (
    Category varchar(128) NOT NULL PRIMARY KEY,
    PolicyReference nvarchar(2000) NOT NULL
);
INSERT INTO @Groups VALUES
    ('Permits and site setup',
        N'Require a tank-removal or demolition project link for setup costs.'),
    ('Fuel and tank preparation',
        N'Require removal or closure context. Routine cleaning alone is insufficient.'),
    ('Demolition and disposal',
        N'Flag retirement wording. Retain the affected asset and site reference.'),
    ('Closure evidence',
        N'Link reports and testing to closure. Review separate remediation obligations.'),
    ('Backfill and surface repair',
        N'Require evidence that tank and associated-asset removal caused the work.');

DECLARE @Keywords TABLE (
    Category varchar(128) NOT NULL,
    Keyword nvarchar(512) NOT NULL,
    PRIMARY KEY (Category, Keyword)
);
INSERT INTO @Keywords VALUES
    ('Permits and site setup', N'Tank / canopy removal permits'),
    ('Permits and site setup', N'demolition permits'),
    ('Permits and site setup', N'equipment mobilization'),
    ('Permits and site setup', N'temporary fencing'),
    ('Permits and site setup', N'electricity disconnection'),
    ('Fuel and tank preparation', N'Remove / relocate fuel from tanks or lines'),
    ('Fuel and tank preparation', N'sludge removal'),
    ('Fuel and tank preparation', N'sludge / liquid disposal'),
    ('Fuel and tank preparation', N'tank inerting'),
    ('Demolition and disposal', N'Remove / demolish / dispose of tanks, canopy or lines'),
    ('Demolition and disposal', N'concrete removal for access'),
    ('Demolition and disposal', N'tank hold-down slab demolition'),
    ('Closure evidence', N'Closure reports'),
    ('Closure evidence', N'soil contamination testing'),
    ('Closure evidence', N'proper waste disposal'),
    ('Closure evidence', N'backfilling records'),
    ('Backfill and surface repair', N'Remove backfill (gravel / sand)'),
    ('Backfill and surface repair', N'waste haulage / disposal'),
    ('Backfill and surface repair', N'backfill holes'),
    ('Backfill and surface repair', N'compaction'),
    ('Backfill and surface repair', N'asphalt reinstatement'),
    ('Backfill and surface repair', N'concrete repair');

DECLARE @Proposed TABLE (
    Keyword nvarchar(512) NOT NULL,
    Category varchar(128) NOT NULL,
    PolicyReference nvarchar(2000) NOT NULL,
    RequiresRemovalContext bit NOT NULL,
    Active bit NOT NULL,
    PRIMARY KEY (Category, Keyword)
);
-- All signals require a supported retirement/removal/closure connection;
-- explicit retirement wording may itself provide that context.
INSERT INTO @Proposed
SELECT keywords.Keyword, keywords.Category, groups.PolicyReference, 1, 1
FROM @Keywords AS keywords JOIN @Groups AS groups ON groups.Category=keywords.Category;
IF (SELECT COUNT_BIG(*) FROM @Proposed)<>(SELECT COUNT_BIG(*) FROM @Keywords)
    THROW 51345, 'An ARO keyword has no supporting review group.', 1;

DECLARE @CurrentRows bigint, @InsertedRows bigint, @FinalRows bigint;
BEGIN TRY
    BEGIN TRANSACTION;
    SELECT @CurrentRows=COUNT_BIG(*) FROM ref.AROIndicator WITH (TABLOCKX, HOLDLOCK);
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @CurrentRows AS CurrentTableRows,
            (SELECT COUNT_BIG(*) FROM @Proposed) AS ProposedRows,
            (SELECT COUNT_BIG(*) FROM @Groups) AS SignalGroups;
        SELECT proposed.*,
            CASE WHEN existing.IndicatorID IS NULL THEN 'INSERT'
                WHEN existing.PolicyReference=proposed.PolicyReference
                    AND existing.RequiresRemovalContext=proposed.RequiresRemovalContext
                    AND existing.Active=proposed.Active THEN 'UNCHANGED'
                ELSE 'CONFLICT' END AS ProposedAction
        FROM @Proposed AS proposed
        LEFT JOIN ref.AROIndicator AS existing
            ON existing.Category=proposed.Category AND existing.Keyword=proposed.Keyword
        ORDER BY proposed.Category, proposed.Keyword;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    -- Do not overwrite an existing reviewed catalog or reactivate disabled rules.
    IF EXISTS (
        SELECT Keyword, Category, PolicyReference, RequiresRemovalContext, Active
        FROM ref.AROIndicator
        EXCEPT
        SELECT Keyword, Category, PolicyReference, RequiresRemovalContext, Active
        FROM @Proposed
    )
        THROW 51346, 'Existing ARO rows conflict with slide 3. Review them before importing.', 1;
    INSERT INTO ref.AROIndicator
        (Keyword, Category, PolicyReference, RequiresRemovalContext, Active)
    SELECT proposed.Keyword, proposed.Category, proposed.PolicyReference,
        proposed.RequiresRemovalContext, proposed.Active
    FROM @Proposed AS proposed
    WHERE NOT EXISTS (
        SELECT 1 FROM ref.AROIndicator AS existing
        WHERE existing.Category=proposed.Category AND existing.Keyword=proposed.Keyword
    );
    SET @InsertedRows=@@ROWCOUNT;
    SELECT @FinalRows=COUNT_BIG(*) FROM ref.AROIndicator;
    IF @FinalRows<>(SELECT COUNT_BIG(*) FROM @Proposed)
        OR @FinalRows<>@CurrentRows+@InsertedRows
        THROW 51347, 'ARO indicator row counts did not reconcile.', 1;
    IF EXISTS (
        SELECT Keyword, Category, PolicyReference, RequiresRemovalContext, Active
        FROM @Proposed
        EXCEPT
        SELECT Keyword, Category, PolicyReference, RequiresRemovalContext, Active
        FROM ref.AROIndicator
    )
        THROW 51348, 'Stored ARO indicators do not match slide 3.', 1;
    COMMIT TRANSACTION;
    SELECT 'ARO_INDICATORS_LOADED' AS Result,
        (SELECT COUNT_BIG(*) FROM @Groups) AS SignalGroups,
        @InsertedRows AS InsertedRows, @FinalRows AS TotalTableRows;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
