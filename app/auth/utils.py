import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
import os
from dotenv import load_dotenv

load_dotenv()


# ----------------------------#01-------------------------------
# password hashing 
def hash_password(password: str)-> str:
    if not password:
        raise ValueError("password can not be empty")

    pass_in_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(pass_in_bytes, salt)

    return hashed.decode('utf-8')

# password verifying
def verify_password(password: str, hashed_password: str)-> bool:
    if not password:
        raise ValueError("password must not be empty")
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))




# --------------------------02---------------------

secret_key = os.getenv("JWT_SECRET_KEY")
algo = "HS256"

def create_access_token(user_id: str)-> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10)
    }

    token = jwt.encode(payload, secret_key, algorithm=algo)
    return token


def verify_access_token(token: str) -> dict:
    decoded = jwt.decode(token, secret_key, algorithms=[algo])
    return decoded


test_token = create_access_token("user01111")

try:
    res = verify_access_token(test_token)
    print("SUCCESS:", res)
except jwt.exceptions.ExpiredSignatureError:
    print("Token expired")
except jwt.exceptions.InvalidTokenError:
    print("Invalid token")
