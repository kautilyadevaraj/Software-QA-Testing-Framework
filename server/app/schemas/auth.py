from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


PASSWORD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,18}$")


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=18)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not PASSWORD_REGEX.match(value):
            raise ValueError(
                "Password must be 8-18 chars with uppercase, lowercase, number, and special character"
            )
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email", "password")
    @classmethod
    def strip_and_require_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Field is required")
        return normalized


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserResponse


class MessageResponse(BaseModel):
    message: str
