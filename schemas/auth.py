from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    identifier: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str


class RegisterRequest(BaseModel):
    correo: EmailStr
    namevar: str
    password: str


class RegisterResponse(BaseModel):
    message: str
    id: int


class CurrentUserResponse(BaseModel):
    id: int
    correo: EmailStr
    namevar: str