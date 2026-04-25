import csv
from typing import List, Dict
from pathlib import Path

def read_credentials_csv(file_path: str) -> List[Dict]:
    credentials = []

    with open(file_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            credentials.append({
                "username": row.get("username"),
                "password": row.get("password"),
                "role": row.get("role"),
                "auth_type": row.get("authtype"),
                "endpoint": row.get("api endpoint"),
                "verified": False
            })

    return credentials