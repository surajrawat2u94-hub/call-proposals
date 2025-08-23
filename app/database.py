import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session


DATA_DIR = "/workspace/data"
DB_PATH = os.path.join(DATA_DIR, "cfps.db")
DB_URL = f"sqlite:///{DB_PATH}"

# Ensure the data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Create engine and session factory
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
	pass


def get_db_session() -> Generator[Session, None, None]:
	"""FastAPI dependency that provides a scoped SQLAlchemy session."""
	session: Session = SessionLocal()
	try:
		yield session
	finally:
		session.close()