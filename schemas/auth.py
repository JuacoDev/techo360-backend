from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    identifier: str  # puede ser correo o usuario
    password: str



class LoginResponse(BaseModel):
    access_token: str
    token_type: str 