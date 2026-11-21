"""Create tables (if needed) and seed a demo user with a root folder.

Run inside the DSC5003 environment:

    python sql/seed.py --username demo --password demo123456

The script is idempotent: an existing username is left untouched.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from services.vfs_storage import VFSStorageService  # noqa: E402
from shared.auth import hash_password  # noqa: E402
from shared.config.settings import Settings  # noqa: E402
from shared.db.models import User  # noqa: E402
from shared.db.session import init_db, make_engine, make_session_factory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the CloudDisk demo user")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.database_url:
        settings = replace(settings, database_url=args.database_url)
    settings.ensure_directories()

    engine = make_engine(settings.database_url, echo=settings.database_echo)
    init_db(engine)
    session_factory = make_session_factory(engine)
    vfs = VFSStorageService(settings)

    with session_factory() as db:
        existing = db.scalar(
            select(User).where(
                func.lower(User.username) == args.username.strip().lower()
            )
        )
        if existing:
            root = vfs.get_root(db, existing.user_id)
            db.commit()
            print(f"用户已存在: {existing.username} (id={existing.user_id})")
            print(f"根目录 node_id={root.node_id}")
            return

        user = User(
            username=args.username.strip(),
            password_hash=hash_password(args.password, settings.bcrypt_rounds),
            status="ACTIVE",
        )
        db.add(user)
        db.flush()
        root = vfs.create_root(db, user.user_id)
        db.commit()
        print(f"已创建用户: {user.username} (id={user.user_id})")
        print(f"密码: {args.password}")
        print(f"根目录 node_id={root.node_id}")
        print("请使用前端登录，或运行 scripts/generate_test_data.py 生成演示数据。")


if __name__ == "__main__":
    main()
