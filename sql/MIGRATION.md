# 从旧版 `disk.sql` 迁移到 CloudDisk schema

旧原型（`sql/legacy_disk.sql`，来自 `GP_NetDisk`）包含三张表：

| 旧表 | 旧字段 | 新表 / 新字段 | 说明 |
|---|---|---|---|
| `user_table` | `user_id` / `user_name` / `salt` / `crypt_passwd` | `users.user_id` / `username` / `password_salt` / `password_hash` | 密码改为 bcrypt-sha256；`salt` 列保留但不再使用 |
| `VFS` | `code` | `vfs_nodes.node_id` | **VFS 节点 ID 就是 file_id** |
| `VFS` | `pre_code` | `vfs_nodes.parent_id` | 根目录为 `NULL`（每个用户自动创建一条根节点） |
| `VFS` | `file_name` | `vfs_nodes.name` | 文件名 / 文件夹名 |
| `VFS` | `file_type` (`'f'` / `'d'`) | `vfs_nodes.node_type` (`FILE` / `FOLDER`) | 枚举值规范化 |
| `VFS` | `file_size` | `vfs_nodes.file_size` + `storage_objects.file_size` | 逻辑大小和物理大小分别保存 |
| `VFS` | `file_md5` | `vfs_nodes.content_md5` + `storage_objects.content_md5` | 物理内容按 MD5 唯一 |
| `VFS` | `is_valid` | `vfs_nodes.status` + `deleted_at` + `delete_token` | 软删除，活动名唯一约束依赖 `delete_token='0'` |
| `VFS` | `owner_uid` | `vfs_nodes.owner_id` | 外键到 `users` |
| `MD5_IP` | `file_md5` / 两个 `ip:port` | `object_locations(object_id, storage_node_id, object_path)` | 多存储节点扩展；第一版只有本地节点 |

## 关键变化

1. **逻辑文件与物理内容分离**：`vfs_nodes` 只保存元数据和 `storage_object_id`，磁盘文件以 MD5 命名并只保存一份。
2. **引用计数**：`storage_objects.ref_count` 记录有多少个活动逻辑文件引用同一份内容；删除最后一个引用时才删除物理文件。
3. **软删除**：删除只修改 `vfs_nodes.status`、`deleted_at`、`delete_token`，搜索最终结果仍由 Gateway 查询 MySQL 过滤。
4. **同文件夹活动名唯一**：依据 `UNIQUE(owner_id, parent_id, name, delete_token)`。活动节点 `delete_token='0'`，删除后写入唯一令牌，因此同名文件可以重新创建。
5. **索引状态**：每个文件一条 `index_status`，记录关键词/语义索引状态、版本、重试次数和最后错误。

## 迁移步骤（示例）

```sql
-- 1. 创建新库并建表
CREATE DATABASE clouddisk CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
-- 运行 sql/schema.sql

-- 2. 导入旧用户（密码哈希格式不同，建议让用户重置密码）
INSERT INTO users (user_id, username, password_hash, password_salt, status)
SELECT user_id, user_name, CONCAT('bcrypt_sha256$', crypt_passwd), salt, 'ACTIVE'
FROM legacy.user_table;

-- 3. 为每个用户创建根目录
INSERT INTO vfs_nodes (owner_id, parent_id, node_type, name, file_size, status, delete_token)
SELECT user_id, NULL, 'FOLDER', '我的文件', 0, 'ACTIVE', '0' FROM users;

-- 4. 迁移文件节点时需要先按 file_md5 去重创建 storage_objects，
--    再逐条建立 vfs_nodes 并设置 ref_count。建议使用项目中的
--    Python 数据迁移脚本（参照 sql/seed.py 的构造方式）来完成。
```

旧系统的物理文件可直接复制到 `data/objects/ab/cd/<md5>`；两字符分片目录规则与新存储一致。
