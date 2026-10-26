CREATE DATABASE IF NOT EXISTS idm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE idm;

CREATE TABLE IF NOT EXISTS users (
    uid BINARY(16) NOT NULL PRIMARY KEY,
    username VARCHAR(255) NOT NULL UNIQUE,
    password VARCHAR(255) NULL,
    role ENUM('administrator', 'maintainer') NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE
);
