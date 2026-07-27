'''
Randon test file not production 
'''



import jwt
import secrets
from datetime import datetime, timedelta, timezone

secret_key = secrets.token_hex(32)
algorithm = "HS256"

payload = {
    "sub": 222,
    "exp": datetime.now(timezone.utc) + timedelta(seconds=10)
}

token = jwt.encode(payload, secret_key, algorithm=algorithm)
