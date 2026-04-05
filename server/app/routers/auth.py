import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import clear_auth_cookies, decode_token, set_auth_cookies
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, MessageResponse, SignupRequest, UserResponse
from app.services.auth_service import authenticate_user, create_user, get_user_by_email, issue_tokens_for_user
from app.utils.rate_limiter import limiter


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.rate_limit_auth)
def signup(
    request: Request,
    response: Response,
    payload: SignupRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    existing = get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This email already exists")

    user = create_user(db, payload.email, payload.password)
    access_token, refresh_token = issue_tokens_for_user(user.id)
    set_auth_cookies(response, access_token, refresh_token)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
@limiter.limit(settings.rate_limit_auth)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token, refresh_token = issue_tokens_for_user(user.id)
    set_auth_cookies(response, access_token, refresh_token)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/refresh", response_model=MessageResponse)
@limiter.limit(settings.rate_limit_auth)
def refresh_token(request: Request, response: Response) -> MessageResponse:
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")

    payload = decode_token(token, expected_type="refresh")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from None

    access_token, refresh_token_value = issue_tokens_for_user(user_id)
    set_auth_cookies(response, access_token, refresh_token_value)
    return MessageResponse(message="Session refreshed")


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response) -> MessageResponse:
    clear_auth_cookies(response)
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
