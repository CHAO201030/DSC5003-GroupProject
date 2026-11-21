-- ===========================================================================
-- CloudDisk MySQL 8 schema (source of truth for the production database)
--
-- Create the database first, then run this file:
--
--   CREATE DATABASE clouddisk CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
--   mysql -u clouddisk -p clouddisk < sql/schema.sql
--
-- The legacy prototype schema (user_table / VFS / MD5_IP) is kept in
-- sql/legacy_disk.sql and mapped to the new tables in sql/MIGRATION.md.
-- ===========================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
  `user_id`       INT          NOT NULL AUTO_INCREMENT,
  `username`      VARCHAR(64)  NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `password_salt` VARCHAR(64)  NULL,
  `status`        VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uq_users_username` (`username`),
  CONSTRAINT `ck_users_user_status` CHECK (`status` IN ('ACTIVE','DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- storage_nodes (one local node in v1; ready for multi-node expansion)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `storage_nodes` (
  `node_id`           INT          NOT NULL AUTO_INCREMENT,
  `hostname`          VARCHAR(128) NOT NULL,
  `port`              INT          NOT NULL DEFAULT 0,
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'ONLINE',
  `capacity_bytes`    BIGINT       NOT NULL DEFAULT 0,
  `used_bytes`        BIGINT       NOT NULL DEFAULT 0,
  `last_heartbeat_at` DATETIME     NULL,
  `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`node_id`),
  CONSTRAINT `ck_storage_nodes_storage_status` CHECK (`status` IN ('ONLINE','OFFLINE','FULL')),
  CONSTRAINT `ck_storage_nodes_storage_capacity` CHECK (`capacity_bytes` >= 0 AND `used_bytes` >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- storage_objects (physical content, unique per MD5)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `storage_objects` (
  `object_id`       INT          NOT NULL AUTO_INCREMENT,
  `content_md5`     CHAR(32)     NOT NULL,
  `file_size`       BIGINT       NOT NULL,
  `storage_key`     VARCHAR(255) NOT NULL,
  `storage_backend` VARCHAR(32)  NOT NULL DEFAULT 'local',
  `ref_count`       INT          NOT NULL DEFAULT 0,
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`object_id`),
  UNIQUE KEY `uq_storage_objects_content_md5` (`content_md5`),
  KEY `ix_storage_objects_storage_key` (`storage_key`),
  CONSTRAINT `ck_storage_objects_object_size` CHECK (`file_size` >= 0),
  CONSTRAINT `ck_storage_objects_object_ref_count` CHECK (`ref_count` >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- vfs_nodes (logical files and folders; file_id == node_id)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `vfs_nodes` (
  `node_id`           INT          NOT NULL AUTO_INCREMENT,
  `owner_id`          INT          NOT NULL,
  `parent_id`         INT          NULL,
  `node_type`         VARCHAR(8)   NOT NULL,
  `name`              VARCHAR(255) NOT NULL,
  `storage_object_id` INT          NULL,
  `file_size`         BIGINT       NOT NULL DEFAULT 0,
  `mime_type`         VARCHAR(128) NULL,
  `content_md5`       CHAR(32)     NULL,
  `version`           INT          NOT NULL DEFAULT 1,
  `status`            VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
  `deleted_at`        DATETIME     NULL,
  `delete_token`      VARCHAR(64)  NOT NULL DEFAULT '0',
  `description`       TEXT         NULL,
  `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`node_id`),
  UNIQUE KEY `uq_vfs_active_name` (`owner_id`, `parent_id`, `name`, `delete_token`),
  KEY `ix_vfs_nodes_owner_id` (`owner_id`),
  KEY `ix_vfs_owner_parent_status` (`owner_id`, `parent_id`, `status`),
  KEY `ix_vfs_parent_name_token` (`parent_id`, `name`, `delete_token`),
  KEY `ix_vfs_content_md5` (`content_md5`),
  KEY `ix_vfs_updated_at` (`updated_at`),
  CONSTRAINT `fk_vfs_nodes_owner_id` FOREIGN KEY (`owner_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_vfs_nodes_parent_id` FOREIGN KEY (`parent_id`) REFERENCES `vfs_nodes` (`node_id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_vfs_nodes_storage_object_id` FOREIGN KEY (`storage_object_id`) REFERENCES `storage_objects` (`object_id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_vfs_nodes_vfs_node_type` CHECK (`node_type` IN ('FILE','FOLDER')),
  CONSTRAINT `ck_vfs_nodes_vfs_status` CHECK (`status` IN ('ACTIVE','DELETED')),
  CONSTRAINT `ck_vfs_nodes_vfs_file_size` CHECK (`file_size` >= 0),
  CONSTRAINT `ck_vfs_nodes_vfs_version` CHECK (`version` >= 1),
  CONSTRAINT `ck_vfs_nodes_vfs_storage_object_required` CHECK (
    (`node_type` = 'FOLDER' AND `storage_object_id` IS NULL)
    OR (`node_type` = 'FILE' AND `storage_object_id` IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- object_locations (physical replicas of an object on storage nodes)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `object_locations` (
  `location_id`     INT          NOT NULL AUTO_INCREMENT,
  `object_id`       INT          NOT NULL,
  `storage_node_id` INT          NOT NULL,
  `replica_role`    VARCHAR(16)  NOT NULL DEFAULT 'PRIMARY',
  `object_path`     VARCHAR(512) NOT NULL,
  `status`          VARCHAR(16)  NOT NULL DEFAULT 'PRESENT',
  `last_synced_at`  DATETIME     NULL,
  PRIMARY KEY (`location_id`),
  UNIQUE KEY `uq_object_location` (`object_id`, `storage_node_id`, `replica_role`),
  KEY `ix_object_locations_storage_node_id` (`storage_node_id`),
  CONSTRAINT `fk_object_locations_object_id` FOREIGN KEY (`object_id`) REFERENCES `storage_objects` (`object_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_object_locations_storage_node_id` FOREIGN KEY (`storage_node_id`) REFERENCES `storage_nodes` (`node_id`) ON DELETE CASCADE,
  CONSTRAINT `ck_object_locations_location_status` CHECK (`status` IN ('PRESENT','MISSING','SYNCING'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- file_permissions (reserved for optional sharing in a later stage)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `file_permissions` (
  `permission_id`   INT         NOT NULL AUTO_INCREMENT,
  `node_id`         INT         NOT NULL,
  `user_id`         INT         NOT NULL,
  `permission_type` VARCHAR(16) NOT NULL DEFAULT 'READ',
  `granted_by`      INT         NULL,
  `created_at`      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`permission_id`),
  UNIQUE KEY `uq_file_permission` (`node_id`, `user_id`),
  KEY `ix_file_permissions_user_id` (`user_id`),
  CONSTRAINT `fk_file_permissions_node_id` FOREIGN KEY (`node_id`) REFERENCES `vfs_nodes` (`node_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_file_permissions_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_file_permissions_granted_by` FOREIGN KEY (`granted_by`) REFERENCES `users` (`user_id`) ON DELETE SET NULL,
  CONSTRAINT `ck_file_permissions_permission_type` CHECK (`permission_type` IN ('READ','WRITE','OWNER'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- share_links (reserved for optional sharing in a later stage)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `share_links` (
  `share_id`   INT         NOT NULL AUTO_INCREMENT,
  `node_id`    INT         NOT NULL,
  `token`      VARCHAR(64) NOT NULL,
  `permission` VARCHAR(16) NOT NULL DEFAULT 'READ',
  `created_by` INT         NOT NULL,
  `expires_at` DATETIME    NULL,
  `revoked_at` DATETIME    NULL,
  `created_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`share_id`),
  UNIQUE KEY `uq_share_links_token` (`token`),
  KEY `ix_share_links_node_id` (`node_id`),
  CONSTRAINT `fk_share_links_node_id` FOREIGN KEY (`node_id`) REFERENCES `vfs_nodes` (`node_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_share_links_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `ck_share_links_share_permission` CHECK (`permission` IN ('READ','WRITE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- index_status (one row per file node)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `index_status` (
  `file_id`                INT          NOT NULL,
  `current_version`        INT          NOT NULL DEFAULT 1,
  `keyword_status`         VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
  `semantic_status`        VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
  `keyword_index_version`  INT          NOT NULL DEFAULT 0,
  `semantic_model_version` VARCHAR(128) NULL,
  `retry_count`            INT          NOT NULL DEFAULT 0,
  `last_error`             TEXT         NULL,
  `updated_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`file_id`),
  KEY `ix_index_status_keyword_status` (`keyword_status`),
  KEY `ix_index_status_semantic_status` (`semantic_status`),
  CONSTRAINT `fk_index_status_file_id` FOREIGN KEY (`file_id`) REFERENCES `vfs_nodes` (`node_id`) ON DELETE CASCADE,
  CONSTRAINT `ck_index_status_keyword_status` CHECK (`keyword_status` IN ('PENDING','INDEXING','READY','FAILED')),
  CONSTRAINT `ck_index_status_semantic_status` CHECK (`semantic_status` IN ('PENDING','INDEXING','READY','FAILED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- search_logs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `search_logs` (
  `log_id`       INT         NOT NULL AUTO_INCREMENT,
  `user_id`      INT         NOT NULL,
  `query_text`   TEXT        NOT NULL,
  `search_mode`  VARCHAR(16) NOT NULL,
  `result_count` INT         NOT NULL DEFAULT 0,
  `elapsed_ms`   INT         NOT NULL DEFAULT 0,
  `created_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`log_id`),
  KEY `ix_search_logs_user_created` (`user_id`, `created_at`),
  KEY `ix_search_logs_created_at` (`created_at`),
  CONSTRAINT `fk_search_logs_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `ck_search_logs_search_mode` CHECK (`search_mode` IN ('keyword','filename','content','semantic')),
  CONSTRAINT `ck_search_logs_search_log_metrics` CHECK (`result_count` >= 0 AND `elapsed_ms` >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
