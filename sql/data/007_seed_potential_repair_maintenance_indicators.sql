SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Replaces all rows in this table and resets IndicatorID to 1. Preview before applying.
DECLARE @ApplyChanges bit = 1;
DECLARE @ExpectedCurrentRows bigint = 31;
IF DB_NAME() <> N'cict-ai-cus-sqldb' OR @@TRANCOUNT <> 0
    THROW 51320, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @ApplyChanges IS NULL
    THROW 51321, 'ApplyChanges must explicitly be 0 or 1.', 1;
IF @ApplyChanges=1 AND (@ExpectedCurrentRows IS NULL OR @ExpectedCurrentRows<0)
    THROW 51322, 'Set ExpectedCurrentRows to CurrentTableRows from the preview before replacing rows.', 1;
IF OBJECT_ID(N'ref.RepairMaintenanceIndicator', N'U') IS NULL
    THROW 51323, 'Create ref.RepairMaintenanceIndicator with migration 009 first.', 1;

DECLARE @Groups TABLE (
    Category varchar(128) NOT NULL PRIMARY KEY, ReviewContext nvarchar(1000) NOT NULL
);
INSERT INTO @Groups VALUES
    ('Preventive maintenance',
        N'Normally OPEX when work preserves or restores normal operating condition.'),
    ('Diagnostics / troubleshooting',
        N'OPEX when the work does not produce a qualifying component replacement.'),
    ('Minor repairs / consumables',
        N'Routine repairs and consumables are normally OPEX. Assess complete component replacements separately.'),
    ('Routine service costs',
        N'Separate costs directly attributable to a qualifying replacement from routine service.'),
    ('Rework / unsuccessful repairs',
        N'Separate these costs from qualifying replacement and successful installation costs.'),
    ('Removal',
        N'Assess qualifying replacements and directly attributable costs under the Reporting Manual.');

DECLARE @Keywords TABLE (
    Category varchar(128) NOT NULL, Keyword nvarchar(512) NOT NULL,
    PRIMARY KEY (Category, Keyword)
);
INSERT INTO @Keywords VALUES
    ('Preventive maintenance', N'Cleaning'),
    ('Preventive maintenance', N'Lubrication'),
    ('Preventive maintenance', N'Calibration'),
    ('Preventive maintenance', N'Inspection'),
    ('Preventive maintenance', N'Descaling'),
    ('Preventive maintenance', N'Filter changes'),
    ('Diagnostics / troubleshooting', N'Service call'),
    ('Diagnostics / troubleshooting', N'Testing'),
    ('Diagnostics / troubleshooting', N'Fault identification'),
    ('Diagnostics / troubleshooting', N'Adjustment'),
    ('Diagnostics / troubleshooting', N'Troubleshooting'),
    ('Minor repairs / consumables', N'Clear blockages'),
    ('Minor repairs / consumables', N'Seal leaks'),
    ('Minor repairs / consumables', N'Repair wiring'),
    ('Minor repairs / consumables', N'Gaskets'),
    ('Minor repairs / consumables', N'Seals'),
    ('Minor repairs / consumables', N'Hoses'),
    ('Minor repairs / consumables', N'Switches'),
    ('Minor repairs / consumables', N'Fasteners'),
    ('Minor repairs / consumables', N'Filters'),
    ('Minor repairs / consumables', N'Fuses'),
    ('Minor repairs / consumables', N'Belts'),
    ('Routine service costs', N'Technician time'),
    ('Routine service costs', N'Travel'),
    ('Routine service costs', N'Recurring maintenance coverage'),
    ('Rework / unsuccessful repairs', N'Rework'),
    ('Rework / unsuccessful repairs', N'Unsuccessful repair attempts'),
    ('Rework / unsuccessful repairs', N'Waiting time'),
    ('Rework / unsuccessful repairs', N'Wrong-part costs'),
    ('Rework / unsuccessful repairs', N'Unrelated repairs'),
    ('Removal', N'Removal');

DECLARE @Proposed TABLE (
    Keyword nvarchar(512) NOT NULL, Category varchar(128) NOT NULL,
    SignalStrength decimal(19,4) NOT NULL, PolicyReference nvarchar(2000) NOT NULL,
    PRIMARY KEY (Category, Keyword)
);
-- Uniform 1 means keyword presence, not a calibrated probability or approved accounting weight.
INSERT INTO @Proposed
SELECT keywords.Keyword, keywords.Category, CAST(1 AS decimal(19,4)),
    groups.ReviewContext
FROM @Keywords AS keywords JOIN @Groups AS groups ON groups.Category=keywords.Category;
IF (SELECT COUNT_BIG(*) FROM @Proposed)<>(SELECT COUNT_BIG(*) FROM @Keywords)
    THROW 51324, 'A proposed keyword has no supporting review group.', 1;

DECLARE @CurrentRows bigint, @DeletedRows bigint, @InsertedRows bigint, @FinalRows bigint;
DECLARE @FirstIndicatorID int, @LastIndicatorID int;
BEGIN TRY
    BEGIN TRANSACTION;
    SELECT @CurrentRows=COUNT_BIG(*) FROM ref.RepairMaintenanceIndicator WITH (TABLOCKX, HOLDLOCK);
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @CurrentRows AS CurrentTableRows,
            (SELECT COUNT_BIG(*) FROM @Proposed) AS ProposedRows;
        SELECT 'DELETE' AS ProposedAction, *
        FROM ref.RepairMaintenanceIndicator ORDER BY IndicatorID;
        SELECT 'INSERT' AS ProposedAction, *
        FROM @Proposed ORDER BY Category, Keyword;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    IF @CurrentRows<>@ExpectedCurrentRows
        THROW 51325, 'Current row count differs from the preview. Preview again before replacing rows.', 1;
    TRUNCATE TABLE ref.RepairMaintenanceIndicator;
    SET @DeletedRows=@CurrentRows;
    INSERT INTO ref.RepairMaintenanceIndicator (Keyword, Category, SignalStrength, PolicyReference)
        SELECT Keyword, Category, SignalStrength, PolicyReference FROM @Proposed;
    SET @InsertedRows=@@ROWCOUNT;
    SELECT @FinalRows=COUNT_BIG(*), @FirstIndicatorID=MIN(IndicatorID),
        @LastIndicatorID=MAX(IndicatorID) FROM ref.RepairMaintenanceIndicator;
    IF @InsertedRows<>(SELECT COUNT_BIG(*) FROM @Proposed) OR @FinalRows<>@InsertedRows
        OR @FirstIndicatorID<>1 OR @LastIndicatorID<>@FinalRows
        THROW 51327, 'Indicator row counts or identity range did not reconcile.', 1;
    IF EXISTS (
        SELECT Keyword, Category, SignalStrength, PolicyReference FROM @Proposed
        EXCEPT
        SELECT Keyword, Category, SignalStrength, PolicyReference FROM ref.RepairMaintenanceIndicator
    )
        THROW 51328, 'Stored indicators do not match the proposed slide catalog.', 1;
    COMMIT TRANSACTION;
    SELECT 'SLIDE_4_REPAIR_INDICATORS_LOADED' AS Result,
        (SELECT COUNT_BIG(*) FROM @Proposed) AS Slide4Keywords,
        (SELECT COUNT_BIG(*) FROM @Groups) AS SignalGroups,
        @DeletedRows AS DeletedRows, @InsertedRows AS InsertedRows,
        @FinalRows AS TotalTableRows,
        @FirstIndicatorID AS FirstIndicatorID, @LastIndicatorID AS LastIndicatorID;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
