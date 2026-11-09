import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ravn.db")


def create_database_engine(database_url: str):
    """Create an engine with only the connection options required by its dialect."""
    engine_options = {}
    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **engine_options)


engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
