-- Seed a local SQL Server to mimic a Microsoft Fabric Warehouse for a live
-- fabric-assess scan. Creates tables with a mix of clean and lossy types, a
-- view with T-SQL-specific constructs, and a stored procedure with MERGE.
CREATE DATABASE SalesWH;
GO
USE SalesWH;
GO

CREATE TABLE dbo.customers (
    id          INT NOT NULL,
    name        NVARCHAR(100),
    email       NVARCHAR(200),
    created_at  DATETIME2
);
GO

CREATE TABLE dbo.orders (
    id          BIGINT NOT NULL,
    customer_id INT NOT NULL,
    amount      MONEY,            -- lossy -> decimal(19,4)
    legacy_ts   DATETIME,         -- lossy precision -> timestamp
    notes       TEXT,             -- lossy LOB -> string
    updated_at  DATETIME2         -- sync signal
);
GO

CREATE VIEW dbo.v_customer_orders AS
    SELECT TOP 100
        c.name,
        STRING_AGG(CONVERT(VARCHAR, o.id), ',') AS order_ids,
        SUM(o.amount) AS total
    FROM dbo.customers c
    CROSS APPLY (SELECT * FROM dbo.orders o WHERE o.customer_id = c.id) o
    WHERE o.amount > 100
    GROUP BY c.name;
GO

CREATE PROCEDURE dbo.p_upsert_customer
    @id INT, @name NVARCHAR(100)
AS
BEGIN
    MERGE INTO dbo.customers AS t
    USING (SELECT @id AS id, @name AS name) AS s
    ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET t.name = s.name
    WHEN NOT MATCHED THEN INSERT (id, name) VALUES (s.id, s.name);
END;
GO
