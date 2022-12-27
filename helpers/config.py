import os

from aws_lambda_powertools.utilities import parameters


class Config:
    def __init__(self):
        self._scores_table: str = os.environ.get("SCORES_TABLE")
        self._twilio_auth_token: str = parameters.get_secret(
            os.environ.get("TWILIO_AUTH_TOKEN")
        )
        self._webhook_token: str = parameters.get_secret(
            os.environ.get("WEBHOOK_TOKEN")
        )

        # Puzzle 550 was 19347 days since epoch
        self._reference: dict = {
            "puzzle_number": 550,
            "days_since_epoch": 19347,
        }

        self._tz_api = os.environ.get("TZ_API")

    @property
    def scores_table(self) -> str:
        return self._scores_table

    @property
    def twilio_auth_token(self) -> str:
        return self._twilio_auth_token

    @property
    def webhook_token(self) -> str:
        return self._webhook_token

    @property
    def reference(self) -> str:
        return self._reference

    @property
    def tz_api(self) -> str:
        return self._tz_api
