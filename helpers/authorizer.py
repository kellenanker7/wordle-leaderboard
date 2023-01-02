from functools import wraps

from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError


config = Config()
logger = Logger()


def authorize(app):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                assert (
                    app.current_event.query_string_parameters["token"]
                    == config.twilio_account_sid
                )
            except:
                raise UnauthorizedError("Unauthorized")
            return func(*args, **kwargs)

        return wrapper

    return decorator
