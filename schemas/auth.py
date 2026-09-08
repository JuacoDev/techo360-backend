from pydantic import BaseModel, EmailStr, constr

class LoginRequest(BaseModel):
    identifier: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str

class RegisterRequest(BaseModel):
    correo: EmailStr
    namevar: constr(min_length=3, max_length=20)
    password: constr(min_length=6)

class RegisterResponse(BaseModel):
    message: str
    id: int
    access_token: str
    token_type: str

class CurrentUserResponse(BaseModel):
    id: int
    correo: EmailStr
    namevar: str
