from pydantic import BaseModel, Field, field_validator

class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if '@' not in value or value.startswith('@') or value.endswith('@'):
            raise ValueError('Enter a valid email address.')
        return value

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if '@' not in value or value.startswith('@') or value.endswith('@'):
            raise ValueError('Enter a valid email address.')
        return value
