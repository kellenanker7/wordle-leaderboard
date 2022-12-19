from functools import wraps
from urllib.parse import parse_qs

from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError

from twilio.request_validator import RequestValidator

config = Config()
logger = Logger()


def authorize(app):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                uri: str = f"{app.current_event.headers['x-forwarded-proto']}://{app.current_event.headers['host']}{app.current_event.path}"
                logger.debug(uri)

                # assert RequestValidator(token=config.twilio_auth_token).validate(
                #     uri=uri,
                #     signature=app.current_event.headers[config.auth_header],
                #     params=None,
                # )
                return func(*args, **kwargs)

            except Exception as e:
                logger.error(f"Error validating request: {e}")
                raise UnauthorizedError("UnauthorizedError")

        return wrapper

    return decorator
