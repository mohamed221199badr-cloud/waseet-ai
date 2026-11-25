"""Create (or reset the password of) the owner of a business.

    python -m app.create_owner --slug my-shop --name "My Shop" --email me@example.com
The password is read from the WASEET_PASSWORD variable or typed at a hidden prompt,
never from the command line (shell history).
"""

import argparse
import getpass
import os

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Business, User


def run(slug: str, name: str, email: str, password: str) -> None:
    db = SessionLocal()
    try:
        business = db.scalar(select(Business).where(Business.slug == slug))
        if business is None:
            business = Business(name=name, slug=slug)
            db.add(business)
            db.flush()
        email = email.strip().lower()
        user = db.scalar(select(User).where(User.business_id == business.id, User.email == email))
        if user is None:
            user = User(business_id=business.id, full_name=name, email=email, role="owner")
            db.add(user)
        user.password_hash = hash_password(password)
        db.commit()
        print(f"owner ready: {email} @ {slug}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--email", required=True)
    args = ap.parse_args()
    pw = os.environ.get("WASEET_PASSWORD") or getpass.getpass("Password: ")
    if len(pw) < 8:
        raise SystemExit("password must be at least 8 characters")
    run(args.slug, args.name, args.email, pw)
