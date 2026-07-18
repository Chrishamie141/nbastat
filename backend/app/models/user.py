from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str
    email: str
    password_hash: str
    created_at: str
    updated_at: str
    last_login_at: str | None
    is_active: bool = True
