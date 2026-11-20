-- PAUSED DRAFT: do not apply pending PO/bundling business decisions.
-- DBA handoff: allow unknown source facts; no new tables or row updates.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit = 0;
IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0 OR @ApplyChanges IS NULL
    THROW 51600, 'Use cict-ai-cus-sqldb outside a transaction and explicitly select preview/apply.', 1;
DECLARE @Expected TABLE(TableName sysname,ColumnName sysname,TypeName sysname,MaxLength smallint);
INSERT @Expected VALUES
    (N'Project',N'ProjectTotalBudget',N'decimal',9),
    (N'Project',N'BundlingInScope',N'bit',1),
    (N'PurchaseOrderLine',N'AFEProjectID',N'nvarchar',256),
    (N'PurchaseOrderLine',N'SiteID',N'nvarchar',256),
    (N'PurchaseOrderLine',N'POTaskCode',N'nvarchar',512),
    (N'PurchaseOrderLine',N'VendorID',N'nvarchar',256);
IF EXISTS (
    SELECT 1 FROM @Expected e LEFT JOIN sys.columns c
        ON c.object_id=OBJECT_ID(N'prep.'+e.TableName,N'U') AND c.name=e.ColumnName
    WHERE c.column_id IS NULL OR TYPE_NAME(c.system_type_id)<>e.TypeName
        OR c.max_length<>e.MaxLength OR c.is_computed=1 OR c.is_identity=1
        OR (e.TypeName=N'decimal' AND (c.precision<>19 OR c.scale<>4))
) THROW 51601, 'Project/PO-line schema differs from migrations 009; obtain DBA review.', 1;
IF @ApplyChanges=0
BEGIN
    SELECT N'PREVIEW_ONLY' AS ResultType,N'prep.'+e.TableName AS TableName,e.ColumnName,
        c.is_nullable AS CurrentlyNullable,CAST(1 AS bit) AS RequiredNullable,
        CASE WHEN c.is_nullable=1 THEN N'ALREADY_NULLABLE' ELSE N'MAKE_NULLABLE; no data updates' END AS Action
    FROM @Expected e JOIN sys.columns c
        ON c.object_id=OBJECT_ID(N'prep.'+e.TableName,N'U') AND c.name=e.ColumnName
    ORDER BY e.TableName,e.ColumnName;
    RETURN;
END;
BEGIN TRY
    BEGIN TRANSACTION;
    ALTER TABLE prep.Project ALTER COLUMN ProjectTotalBudget decimal(19,4) NULL;
    ALTER TABLE prep.Project ALTER COLUMN BundlingInScope bit NULL;
    ALTER TABLE prep.PurchaseOrderLine ALTER COLUMN AFEProjectID nvarchar(128) NULL;
    ALTER TABLE prep.PurchaseOrderLine ALTER COLUMN SiteID nvarchar(128) NULL;
    ALTER TABLE prep.PurchaseOrderLine ALTER COLUMN POTaskCode nvarchar(256) NULL;
    ALTER TABLE prep.PurchaseOrderLine ALTER COLUMN VendorID nvarchar(128) NULL;
    IF EXISTS (
        SELECT 1 FROM @Expected e JOIN sys.columns c
            ON c.object_id=OBJECT_ID(N'prep.'+e.TableName,N'U') AND c.name=e.ColumnName
        WHERE c.is_nullable<>1
    ) THROW 51602, 'Nullable-field schema readback failed.', 1;
    COMMIT TRANSACTION;
    SELECT N'APPLIED' AS ResultType,N'Unknown project/PO-line fields may now remain NULL; no rows changed.' AS Reason;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
