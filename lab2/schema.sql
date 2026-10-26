CREATE DATABASE IF NOT EXISTS idm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE idm;

CREATE TABLE IF NOT EXISTS users (
    uid CHAR(36) PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NULL, -- hash bcrypt; NULL până la primul login
    role ENUM('admin', 'maintainer', 'user') NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
