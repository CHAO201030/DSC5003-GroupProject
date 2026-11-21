# CloudDisk 数据库 ER 图

> 与 `sql/schema.sql` 和 `shared/db/models.py` 一一对应。

```mermaid
erDiagram
    USERS ||--o{ VFS_NODES : owns
    USERS ||--o{ FILE_PERMISSIONS : receives
    USERS ||--o{ SHARE_LINKS : creates
    USERS ||--o{ SEARCH_LOGS : performs

    VFS_NODES ||--o{ VFS_NODES : parent_of
    VFS_NODES }o--o| STORAGE_OBJECTS : references
    VFS_NODES ||--o| INDEX_STATUS : has
    VFS_NODES ||--o{ FILE_PERMISSIONS : grants
    VFS_NODES ||--o{ SHARE_LINKS : shared

    STORAGE_OBJECTS ||--o{ OBJECT_LOCATIONS : stored_as
    STORAGE_NODES ||--o{ OBJECT_LOCATIONS : hosts

    USERS {
        int user_id PK
        varchar username UK
        varchar password_hash
        varchar password_salt
        varchar status
        datetime created_at
        datetime updated_at
    }

    VFS_NODES {
        int node_id PK
        int owner_id FK
        int parent_id FK
        varchar node_type "FILE | FOLDER"
        varchar name
        int storage_object_id FK
        bigint file_size
        varchar mime_type
        char content_md5
        int version
        varchar status "ACTIVE | DELETED"
        datetime deleted_at
        varchar delete_token
        text description
        datetime created_at
        datetime updated_at
    }

    STORAGE_OBJECTS {
        int object_id PK
        char content_md5 UK
        bigint file_size
        varchar storage_key
        varchar storage_backend
        int ref_count
        datetime created_at
        datetime updated_at
    }

    STORAGE_NODES {
        int node_id PK
        varchar hostname
        int port
        varchar status
        bigint capacity_bytes
        bigint used_bytes
        datetime last_heartbeat_at
    }

    OBJECT_LOCATIONS {
        int location_id PK
        int object_id FK
        int storage_node_id FK
        varchar replica_role
        varchar object_path
        varchar status
        datetime last_synced_at
    }

    FILE_PERMISSIONS {
        int permission_id PK
        int node_id FK
        int user_id FK
        varchar permission_type
        int granted_by FK
        datetime created_at
    }

    SHARE_LINKS {
        int share_id PK
        int node_id FK
        varchar token UK
        varchar permission
        int created_by FK
        datetime expires_at
        datetime revoked_at
        datetime created_at
    }

    INDEX_STATUS {
        int file_id PK
        int current_version
        varchar keyword_status
        varchar semantic_status
        int keyword_index_version
        varchar semantic_model_version
        int retry_count
        text last_error
        datetime updated_at
    }

    SEARCH_LOGS {
        int log_id PK
        int user_id FK
        varchar search_mode
        text query_text
        int result_count
        int elapsed_ms
        datetime created_at
    }
```

## 关键约束

| 约束 | 实现 |
|---|---|
| 用户名唯一 | `UNIQUE(users.username)`，应用层大小写不敏感检查 |
| MD5 存储对象唯一 | `UNIQUE(storage_objects.content_md5)` |
| 同一文件夹活动名唯一 | `UNIQUE(owner_id, parent_id, name, delete_token)`，活动节点 `delete_token='0'` |
| 文件必须有存储对象 | CHECK: `FILE` ⇔ `storage_object_id IS NOT NULL` |
| 文件夹不能有存储对象 | 同上 CHECK 的反向条件 |
| 引用计数非负 | `CHECK(ref_count >= 0)` |
| 大小非负 | `CHECK(file_size >= 0)` |
| 索引状态有限值 | `CHECK(status IN ('PENDING','INDEXING','READY','FAILED'))` |
| 搜索结果过滤 | Gateway 用 `vfs_nodes.owner_id + status='ACTIVE'` 二次查询 |
