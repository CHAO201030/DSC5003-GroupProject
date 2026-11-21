-- Run as a MySQL administrator (e.g. `sudo mysql < sql/create_database.sql`).
-- Change the password before using this outside a local course environment.

CREATE DATABASE IF NOT EXISTS `clouddisk`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

-- Use the server's default auth plugin (MySQL 8: caching_sha2_password).
-- PyMySQL needs the `cryptography` package for this; environment.yml installs it.
CREATE USER IF NOT EXISTS 'clouddisk'@'localhost'
  IDENTIFIED BY 'clouddisk';
CREATE USER IF NOT EXISTS 'clouddisk'@'127.0.0.1'
  IDENTIFIED BY 'clouddisk';

GRANT ALL PRIVILEGES ON `clouddisk`.* TO 'clouddisk'@'localhost';
GRANT ALL PRIVILEGES ON `clouddisk`.* TO 'clouddisk'@'127.0.0.1';
FLUSH PRIVILEGES;
