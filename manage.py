"""LifeTrack 用户管理工具"""
import sys
import hashlib
import secrets
from main import engine, Session, User


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return h.hex(), salt


def create_user(username: str, password: str, admin: bool = False):
    db = Session(engine)
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        print(f"用户 '{username}' 已存在")
        db.close()
        return

    pw_hash, salt = hash_password(password)
    user = User(username=username, password_hash=pw_hash, salt=salt, is_admin=admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"用户已创建: {username} (id={user.id}, admin={user.is_admin})")
    db.close()


def list_users():
    db = Session(engine)
    users = db.query(User).all()
    if not users:
        print("暂无用户")
    for u in users:
        print(f"  id={u.id}  {u.username}  {'[admin]' if u.is_admin else ''}")
    db.close()


def migrate_data():
    """将旧数据(user_id=NULL)归属到admin(id=1)"""
    db = Session(engine)
    from main import Activity, Report, Goal
    for model in [Activity, Report, Goal]:
        count = db.query(model).filter(model.user_id == None).update({model.user_id: 1})
        if count:
            print(f"  已迁移 {model.__tablename__}: {count} 条")
    db.commit()
    db.close()
    print("迁移完成")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python manage.py seed                     创建默认管理员(admin/admin123) + 迁移旧数据")
        print("  python manage.py create <用户名> <密码>    创建普通用户")
        print("  python manage.py create <用户名> <密码> --admin  创建管理员")
        print("  python manage.py list                    列出所有用户")
        print("  python manage.py migrate                 迁移旧数据到admin")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "seed":
        create_user("admin", "admin123", admin=True)
        migrate_data()
    elif cmd == "migrate":
        migrate_data()
    elif cmd == "list":
        list_users()
    elif cmd == "create" and len(sys.argv) >= 4:
        username = sys.argv[2]
        password = sys.argv[3]
        admin = "--admin" in sys.argv
        create_user(username, password, admin)
    else:
        print("无效命令")
