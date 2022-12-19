import functools.wraps

from helpers.config import Config

from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError

from twilio.request_validator import RequestValidator

config = Config()


def authorize(app):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                assert RequestValidator(config.twilio_auth_token).validate(
                    app.url, app.body, app.headers["X-TWILIO-SIGNATURE"]
                )
            except:
                raise UnauthorizedError("Unauthorized request")

            return func(*args, **kwargs)

        return wrapper

    return decorator
