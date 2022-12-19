import os

from aws_lambda_powertools.utilities import parameters


class Config:
    def __init__(self):
        self._ddb_table: str = os.environ.get("DDB_TABLE")
        self._twilio_auth_token: str = parameters.get_secret(
            os.environ.get("TWILIO_AUTH_TOKEN")
        )
        self._auth_header = "x-twilio-signature"

    @property
    def ddb_table(self) -> str:
        return self._ddb_table

    @property
    def twilio_auth_token(self) -> str:
        return self._twilio_auth_token

    @property
    def auth_header(self) -> str:
        return self._auth_header
