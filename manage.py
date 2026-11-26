"""Utilitas administrasi.

Contoh pemakaian:
    python manage.py init-db
    python manage.py create-user admin@isp.local --password Rahasia123 --role admin
    python manage.py create-user viewer@isp.local --role viewer      # password ditanya interaktif
    python manage.py list-users
    python manage.py set-role viewer@isp.local admin
    python manage.py set-active viewer@isp.local false
    python manage.py cleanup-logs --days 30
"""

import argparse
import getpass
import sys
from datetime import datetime, timedelta

from sqlalchemy import func

from app import app
from auth import VALID_ROLES, hash_password
from models import ApiLog, User, db


def _find_user(email):
    return User.query.filter(func.lower(User.email) == email.strip().lower()).first()


def cmd_init_db(_args):
    """Buat tabel yang belum ada (tabel lama tidak diubah)."""
    with app.app_context():
        db.create_all()
        print("Tabel di metadata:", ", ".join(sorted(db.metadata.tables)))
        print("init-db selesai.")
    return 0


def cmd_create_user(args):
    with app.app_context():
        email = args.email.strip().lower()

        if _find_user(email):
            print(f"GAGAL: user {email} sudah ada.")
            return 1
        if args.role not in VALID_ROLES:
            print(f"GAGAL: role harus salah satu dari {VALID_ROLES}.")
            return 1

        password = args.password or getpass.getpass("Password: ")
        if len(password) < 8:
            print("GAGAL: password minimal 8 karakter.")
            return 1

        user = User(email=email, password_hash=hash_password(password), role=args.role)
        db.session.add(user)
        db.session.commit()
        print(f"OK: user dibuat -> id={user.id} email={user.email} role={user.role}")
    return 0


def cmd_list_users(_args):
    with app.app_context():
        users = User.query.order_by(User.id).all()
        if not users:
            print("Belum ada user.")
            return 0
        print(f"{'id':<4} {'email':<34} {'role':<8} {'aktif':<6} dibuat")
        print("-" * 80)
        for u in users:
            print(f"{u.id:<4} {u.email:<34} {u.role:<8} {str(u.is_active):<6} {u.created_at}")
    return 0


def cmd_set_role(args):
    with app.app_context():
        if args.role not in VALID_ROLES:
            print(f"GAGAL: role harus salah satu dari {VALID_ROLES}.")
            return 1
        user = _find_user(args.email)
        if user is None:
            print(f"GAGAL: user {args.email} tidak ditemukan.")
            return 1
        user.role = args.role
        db.session.commit()
        print(f"OK: {user.email} sekarang role={user.role}")
    return 0


def cmd_set_active(args):
    with app.app_context():
        user = _find_user(args.email)
        if user is None:
            print(f"GAGAL: user {args.email} tidak ditemukan.")
            return 1
        user.is_active = args.active
        db.session.commit()
        print(f"OK: {user.email} is_active={user.is_active}")
    return 0


def cmd_set_password(args):
    with app.app_context():
        user = _find_user(args.email)
        if user is None:
            print(f"GAGAL: user {args.email} tidak ditemukan.")
            return 1
        password = args.password or getpass.getpass("Password baru: ")
        if len(password) < 8:
            print("GAGAL: password minimal 8 karakter.")
            return 1
        user.password_hash = hash_password(password)
        db.session.commit()
        print(f"OK: password {user.email} diperbarui.")
    return 0


def cmd_cleanup_logs(args):
    """Hapus log yang lebih lama dari N hari."""
    if args.days < 1:
        print("GAGAL: --days harus >= 1.")
        return 1
    cutoff = datetime.now() - timedelta(days=args.days)
    with app.app_context():
        deleted = ApiLog.query.filter(ApiLog.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.session.commit()
    print(f"OK: {deleted} baris api_log lebih lama dari {args.days} hari dihapus.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Administrasi API")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="buat tabel yang belum ada")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("create-user", help="buat akun baru")
    p.add_argument("email")
    p.add_argument("--password", help="kalau kosong, diminta secara interaktif")
    p.add_argument("--role", default="viewer", choices=VALID_ROLES)
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("list-users", help="tampilkan semua akun")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("set-role", help="ubah role akun")
    p.add_argument("email")
    p.add_argument("role", choices=VALID_ROLES)
    p.set_defaults(func=cmd_set_role)

    p = sub.add_parser("set-active", help="aktifkan / nonaktifkan akun")
    p.add_argument("email")
    p.add_argument("active", type=lambda v: v.lower() in ("1", "true", "yes", "y"))
    p.set_defaults(func=cmd_set_active)

    p = sub.add_parser("set-password", help="ganti password akun")
    p.add_argument("email")
    p.add_argument("--password", help="kalau kosong, diminta secara interaktif")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("cleanup-logs", help="hapus audit log lama")
    p.add_argument("--days", type=int, default=30, help="umur log dalam hari (default: 30)")
    p.set_defaults(func=cmd_cleanup_logs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
