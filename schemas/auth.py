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
    password: constr(min_length=8)

class RegisterResponse(BaseModel):
    message: str
    id: int
    access_token: str
    token_type: str

class CurrentUserResponse(BaseModel):
    id: int
    correo: EmailStr
    namevar: str

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    # Mismo min_length=8 que RegisterRequest -- ver AVISO en
    # routers/auth.py::reset_password sobre por que esta regla vive en
    # los dos sitios donde se fija una contraseña nueva.
    new_password: constr(min_length=8)

class ResetPasswordResponse(BaseModel):
    status: str
