from __future__ import annotations

import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


# Always load the local MCP server environment file,
# regardless of the terminal's current directory.
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_FILE)


def get_connection():
    """
    Return a MySQL connection for the shared Blue Horizon database.
    """
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "blue_horizon_db"),
    )
