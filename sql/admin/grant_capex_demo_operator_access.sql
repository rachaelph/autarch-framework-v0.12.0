CREATE SCHEMA [prep] AUTHORIZATION [dbo];

CREATE SCHEMA [task] AUTHORIZATION [dbo];

CREATE SCHEMA [capex] AUTHORIZATION [dbo];

CREATE SCHEMA [review] AUTHORIZATION [dbo];

CREATE SCHEMA [ops] AUTHORIZATION [dbo];

CREATE SCHEMA [ref] AUTHORIZATION [dbo];

CREATE SCHEMA [pub] AUTHORIZATION [dbo];

GRANT CONTROL ON SCHEMA::[prep]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[task]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[capex]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[review]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[ops]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[ref]
TO [v-rphillips@circlek.com];

GRANT CONTROL ON SCHEMA::[pub]
TO [v-rphillips@circlek.com];

GRANT CREATE TABLE,
CREATE VIEW,
CREATE PROCEDURE,
CREATE FUNCTION,
CREATE TYPE,
CREATE SYNONYM
TO [v-rphillips@circlek.com];
