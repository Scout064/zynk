from __future__ import annotations

import secrets
import string

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, hash_password
from app.db.base import get_db
from app.db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

CREDENTIALS_exc = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise CREDENTIALS_exc from None
    username = payload.get("sub")
    if not username:
        raise CREDENTIALS_exc
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        raise CREDENTIALS_exc
    return user


def bootstrap_admin(db: Session) -> str | None:
    """Create the initial admin account on first run; returns password if generated."""
    settings = get_settings()
    if db.query(User).count() > 0:
        return None
    if settings.initial_admin_password:
        password = settings.initial_admin_password
        generated = False
    else:
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(16))
        generated = True
    db.add(User(username="admin", password_hash=hash_password(password), is_admin=True))
    db.commit()
    if generated:
        print("=" * 60)
        print("First run: created user 'admin' with generated password:")
        print(f"    {password}")
        print("(Set ZYNK_INITIAL_ADMIN_PASSWORD to choose your own.)")
        print("=" * 60)
    return password if generated else None


def issue_token(user: User) -> str:
    return create_access_token(user.username, user.is_admin)
