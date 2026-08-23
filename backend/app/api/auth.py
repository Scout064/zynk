from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, issue_token
from app.core.security import hash_password, verify_password
from app.db.base import get_db
from app.db.models import User
from app.services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/token", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).one_or_none()
    if user is None or not verify_password(form.password, user.password_hash):
        audit(db, "login", form.username, "failed login", ok=False)
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    audit(db, "login", user.username, "ok")
    return TokenOut(access_token=issue_token(user), username=user.username)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "is_admin": user.is_admin}


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    audit(db, "user.password_change", user.username, "ok", actor=user.username)
    return {"ok": True}
