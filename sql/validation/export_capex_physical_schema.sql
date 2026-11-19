SET NOCOUNT ON;

-- Catalog metadata only: no invoice data, DDL, grants or application writes.
SELECT
    1 AS snapshotVersion,
    CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')) AS serverName,
    DB_NAME() AS databaseName,
    CONVERT(varchar(33), SYSUTCDATETIME(), 126) + 'Z' AS capturedAtUtc,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION') AS databaseViewDefinition,
    JSON_QUERY((
        SELECT s.name,
            HAS_PERMS_BY_NAME(s.name, 'SCHEMA', 'VIEW DEFINITION') AS viewDefinition
        FROM sys.schemas AS s
        WHERE s.name IN ('prep', 'task', 'capex', 'ref', 'review', 'ops', 'pub')
        ORDER BY s.name
        FOR JSON PATH, INCLUDE_NULL_VALUES
    )) AS schemas,
    JSON_QUERY((
        SELECT s.name AS [schema], t.name, t.temporal_type_desc AS temporalType,
            OBJECT_SCHEMA_NAME(t.history_table_id) AS historySchema,
            OBJECT_NAME(t.history_table_id) AS historyTable,
            HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name),
                'OBJECT', 'VIEW DEFINITION') AS viewDefinition,
            JSON_QUERY((
                SELECT c.column_id AS ordinal, c.name, ts.name AS typeSchema,
                    ty.name AS [type], c.max_length AS maxLengthBytes,
                    c.precision, c.scale, c.is_nullable AS nullable,
                    c.collation_name AS collation, c.is_identity AS [identity],
                    CONVERT(nvarchar(100), ic.seed_value) AS identitySeed,
                    CONVERT(nvarchar(100), ic.increment_value) AS identityIncrement,
                    c.is_computed AS computed, cc.definition AS computedDefinition,
                    cc.is_persisted AS persisted, dc.name AS defaultName,
                    dc.definition AS defaultDefinition, c.is_rowguidcol AS rowGuid,
                    c.is_sparse AS sparse, c.is_hidden AS hidden,
                    c.generated_always_type_desc AS generatedAlways
                FROM sys.columns AS c
                JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
                JOIN sys.schemas AS ts ON ts.schema_id = ty.schema_id
                LEFT JOIN sys.identity_columns AS ic
                    ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                LEFT JOIN sys.computed_columns AS cc
                    ON cc.object_id = c.object_id AND cc.column_id = c.column_id
                LEFT JOIN sys.default_constraints AS dc ON dc.object_id = c.default_object_id
                WHERE c.object_id = t.object_id
                ORDER BY c.column_id
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )) AS columns,
            JSON_QUERY((
                SELECT i.name, i.type_desc AS [type], i.is_primary_key AS primaryKey,
                    i.is_unique_constraint AS uniqueConstraint, i.is_unique AS [unique],
                    i.is_disabled AS disabled, i.has_filter AS hasFilter,
                    i.filter_definition AS filterDefinition,
                    JSON_QUERY((
                        SELECT c.name, ix.key_ordinal AS keyOrdinal,
                            ix.is_descending_key AS descending,
                            ix.is_included_column AS included,
                            ix.partition_ordinal AS partitionOrdinal
                        FROM sys.index_columns AS ix
                        JOIN sys.columns AS c ON c.object_id = ix.object_id
                            AND c.column_id = ix.column_id
                        WHERE ix.object_id = i.object_id AND ix.index_id = i.index_id
                        ORDER BY ix.index_column_id
                        FOR JSON PATH, INCLUDE_NULL_VALUES
                    )) AS columns
                FROM sys.indexes AS i
                WHERE i.object_id = t.object_id AND i.index_id > 0
                    AND i.is_hypothetical = 0
                ORDER BY i.name
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )) AS indexes,
            JSON_QUERY((
                SELECT fk.name, OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS targetSchema,
                    OBJECT_NAME(fk.referenced_object_id) AS targetTable,
                    fk.delete_referential_action_desc AS onDelete,
                    fk.update_referential_action_desc AS onUpdate,
                    fk.is_disabled AS disabled, fk.is_not_trusted AS notTrusted,
                    fk.is_not_for_replication AS notForReplication,
                    JSON_QUERY((
                        SELECT pc.name AS sourceColumn, rc.name AS targetColumn
                        FROM sys.foreign_key_columns AS fc
                        JOIN sys.columns AS pc ON pc.object_id = fc.parent_object_id
                            AND pc.column_id = fc.parent_column_id
                        JOIN sys.columns AS rc ON rc.object_id = fc.referenced_object_id
                            AND rc.column_id = fc.referenced_column_id
                        WHERE fc.constraint_object_id = fk.object_id
                        ORDER BY fc.constraint_column_id
                        FOR JSON PATH, INCLUDE_NULL_VALUES
                    )) AS columns
                FROM sys.foreign_keys AS fk
                WHERE fk.parent_object_id = t.object_id
                ORDER BY fk.name
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )) AS foreignKeys,
            JSON_QUERY((
                SELECT ck.name, ck.definition, ck.is_disabled AS disabled,
                    ck.is_not_trusted AS notTrusted,
                    ck.is_not_for_replication AS notForReplication
                FROM sys.check_constraints AS ck
                WHERE ck.parent_object_id = t.object_id
                ORDER BY ck.name
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )) AS checks
        FROM sys.tables AS t
        JOIN sys.schemas AS s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0
            AND s.name IN ('prep', 'task', 'capex', 'ref', 'review', 'ops', 'pub')
        ORDER BY s.name, t.name
        FOR JSON PATH, INCLUDE_NULL_VALUES
    )) AS tables,
    JSON_QUERY((
        SELECT s.name AS [schema], v.name, m.definition,
            m.is_schema_bound AS schemaBound,
            JSON_QUERY((
                SELECT c.column_id AS ordinal, c.name, ts.name AS typeSchema,
                    ty.name AS [type], c.max_length AS maxLengthBytes,
                    c.precision, c.scale, c.is_nullable AS nullable
                FROM sys.columns AS c
                JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
                JOIN sys.schemas AS ts ON ts.schema_id = ty.schema_id
                WHERE c.object_id = v.object_id
                ORDER BY c.column_id
                FOR JSON PATH, INCLUDE_NULL_VALUES
            )) AS columns
        FROM sys.views AS v
        JOIN sys.schemas AS s ON s.schema_id = v.schema_id
        LEFT JOIN sys.sql_modules AS m ON m.object_id = v.object_id
        WHERE v.is_ms_shipped = 0
            AND s.name IN ('prep', 'task', 'capex', 'ref', 'review', 'ops', 'pub')
        ORDER BY s.name, v.name
        FOR JSON PATH, INCLUDE_NULL_VALUES
    )) AS views
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES;
