"""
Auth API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.ratelimit import (
    REFRESH_LIMIT, REGISTER_LIMIT, client_ip, enforce, enforce_login,
)
from app.db.session import get_db
from app.users.models import User, PrivacySetting
from app.core.security import (
    get_password_hash, verify_password,
    create_access_token, create_refresh_token, decode_token, get_current_user
)
from app.auth.schemas import (
    UserRegister, UserLogin, TokenResponse, TokenRefresh, UserResponse
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister, request: Request, db: AsyncSession = Depends(get_db)
):
    """Register a new user account."""
    # Before the uniqueness check, not after: the 409 below confirms whether an
    # address is registered, so an unthrottled endpoint is also an email
    # oracle. Throttling does not remove that — telling a real user their
    # address is taken is the point of the message — but it does put a ceiling
    # on how much of a list can be tested.
    await enforce("register-ip", client_ip(request), REGISTER_LIMIT)

    # Check email uniqueness
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Create user
    user = User(
        email=data.email,
        username=data.username,
        hashed_password=get_password_hash(data.password),
        full_name=data.full_name,
    )
    db.add(user)
    await db.flush()

    # Create default privacy settings
    privacy = PrivacySetting(user_id=user.id)
    db.add(privacy)

    # Generate tokens
    token_data = {"sub": str(user.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin, request: Request, db: AsyncSession = Depends(get_db)
):
    """Authenticate user and return tokens."""
    # Counted before the password is checked, so a wrong guess costs the same
    # budget as a right one and the limit cannot be probed for free.
    await enforce_login(request, data.email)

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    token_data = {"sub": str(user.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: TokenRefresh, request: Request, db: AsyncSession = Depends(get_db)
):
    """Refresh an access token."""
    # Generous, because the client calls this automatically whenever an access
    # token expires — this is here to bound token-guessing, not real traffic.
    await enforce("refresh-ip", client_ip(request), REFRESH_LIMIT)

    payload = decode_token(data.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    token_data = {"sub": str(user.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user
