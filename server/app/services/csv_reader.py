from typing import List, Dict

from app.services.credential_service import read_credential_rows

def read_credentials_csv(file_path: str) -> List[Dict]:
    return [{**row, "verified": False} for row in read_credential_rows(file_path)]
