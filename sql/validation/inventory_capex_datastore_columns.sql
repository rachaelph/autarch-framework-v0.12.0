SET NOCOUNT ON;

-- Read-only catalog inventory. No application data, DDL or permission changes.
DECLARE @Schemas TABLE (SchemaName sysname PRIMARY KEY);
INSERT INTO @Schemas (SchemaName) VALUES
    (N'capex'), (N'prep'), (N'mvp_batch'), (N'mvp_review'),
    (N'ops'), (N'ref'), (N'review'), (N'task');

-- A zero visible-table count does not prove a schema is empty without metadata access.
SELECT
    DB_NAME() AS DatabaseName,
    requested.SchemaName,
    CASE WHEN s.schema_id IS NULL THEN N'NOT FOUND OR NOT VISIBLE'
         ELSE N'VISIBLE' END AS SchemaVisibility,
    HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DEFINITION')
        AS CanViewDatabaseDefinition,
    HAS_PERMS_BY_NAME(requested.SchemaName, N'SCHEMA', N'VIEW DEFINITION')
        AS CanViewSchemaDefinition,
    (SELECT COUNT(*) FROM sys.tables AS t
     WHERE t.schema_id = s.schema_id AND t.is_ms_shipped = 0)
        AS VisibleTableCount
FROM @Schemas AS requested
LEFT JOIN sys.schemas AS s ON s.name = requested.SchemaName
ORDER BY requested.SchemaName;

-- One row per visible table column; composite FK mappings retain their column order.
SELECT
    s.name AS SchemaName,
    t.name AS TableName,
    c.column_id AS ColumnOrder,
    c.name AS ColumnName,
    CASE
        WHEN ty.is_user_defined = 1
            THEN QUOTENAME(SCHEMA_NAME(ty.schema_id)) + N'.' + QUOTENAME(ty.name)
        WHEN ty.name IN (N'nvarchar', N'nchar', N'varchar', N'char', N'varbinary', N'binary')
            THEN ty.name + N'(' +
                CASE WHEN c.max_length = -1 THEN N'max'
                     ELSE CONVERT(nvarchar(10), CASE WHEN ty.name IN (N'nvarchar', N'nchar')
                          THEN c.max_length / 2 ELSE c.max_length END) END + N')'
        WHEN ty.name IN (N'decimal', N'numeric')
            THEN ty.name + N'(' + CONVERT(nvarchar(10), c.precision) + N',' +
                CONVERT(nvarchar(10), c.scale) + N')'
        WHEN ty.name IN (N'datetime2', N'datetimeoffset', N'time')
            THEN ty.name + N'(' + CONVERT(nvarchar(10), c.scale) + N')'
        WHEN ty.name = N'float'
            THEN ty.name + N'(' + CONVERT(nvarchar(10), c.precision) + N')'
        ELSE ty.name
    END AS DataType,
    c.is_nullable AS IsNullable,
    c.is_identity AS IsIdentity,
    CONVERT(nvarchar(100), identityColumn.seed_value) AS IdentitySeed,
    CONVERT(nvarchar(100), identityColumn.increment_value) AS IdentityIncrement,
    pk.PrimaryKeyName,
    pk.PrimaryKeyColumnOrder,
    dc.name AS DefaultConstraintName,
    dc.definition AS DefaultDefinition,
    c.is_computed AS IsComputed,
    cc.definition AS ComputedDefinition,
    cc.is_persisted AS IsComputedPersisted,
    fk.ForeignKeyMappings,
    c.collation_name AS CollationName,
    t.temporal_type_desc AS TemporalType
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
JOIN @Schemas AS requested ON requested.SchemaName = s.name
JOIN sys.columns AS c ON c.object_id = t.object_id
JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.identity_columns AS identityColumn
    ON identityColumn.object_id = c.object_id AND identityColumn.column_id = c.column_id
LEFT JOIN sys.default_constraints AS dc ON dc.object_id = c.default_object_id
LEFT JOIN sys.computed_columns AS cc
    ON cc.object_id = c.object_id AND cc.column_id = c.column_id
OUTER APPLY (
    SELECT i.name AS PrimaryKeyName, ic.key_ordinal AS PrimaryKeyColumnOrder
    FROM sys.indexes AS i
    JOIN sys.index_columns AS ic
        ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    WHERE i.object_id = c.object_id AND i.is_primary_key = 1
        AND ic.column_id = c.column_id AND ic.key_ordinal > 0
) AS pk
OUTER APPLY (
    SELECT STRING_AGG(CONVERT(nvarchar(max),
        QUOTENAME(keyDefinition.name) + N' [' +
        CONVERT(nvarchar(10), mapping.constraint_column_id) + N']: ' +
        QUOTENAME(OBJECT_SCHEMA_NAME(mapping.referenced_object_id)) + N'.' +
        QUOTENAME(OBJECT_NAME(mapping.referenced_object_id)) + N'.' +
        QUOTENAME(targetColumn.name) +
        N'; disabled=' + CONVERT(nvarchar(1), keyDefinition.is_disabled) +
        N'; notTrusted=' + CONVERT(nvarchar(1), keyDefinition.is_not_trusted)),
        N' | ') WITHIN GROUP (ORDER BY keyDefinition.name, mapping.constraint_column_id)
        AS ForeignKeyMappings
    FROM sys.foreign_key_columns AS mapping
    JOIN sys.foreign_keys AS keyDefinition
        ON keyDefinition.object_id = mapping.constraint_object_id
    JOIN sys.columns AS targetColumn
        ON targetColumn.object_id = mapping.referenced_object_id
        AND targetColumn.column_id = mapping.referenced_column_id
    WHERE mapping.parent_object_id = c.object_id
        AND mapping.parent_column_id = c.column_id
) AS fk
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;
