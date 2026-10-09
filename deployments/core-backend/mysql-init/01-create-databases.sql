-- Runs once, when the MySQL data volume is first initialised.
-- scf_core is created by MYSQL_DATABASE; Flyway also needs the history schema.
CREATE DATABASE IF NOT EXISTS scf_core_history;
