import boto3
import re

from urllib.parse import parse_qsl

from helpers.authorizer import authorize
from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.api_gateway import Response
from aws_lambda_powertools.event_handler.exceptions import BadRequestError


config = Config()
logger = Logger()
app = APIGatewayHttpResolver()

table = boto3.resource("dynamodb").Table(config.ddb_table)

responses: dict = {
    1: "Hole in one!",
    2: "Birdie!",
    3: "Par.",
    4: "Bogey.",
    5: "Double bogey.",
    6: "Triple bogey.",
}

green = "🟩"
yellow = "🟨"
black = "⬛"


@app.post("/post")
@authorize(app)
def post() -> str:
    body: dict = dict(parse_qsl(app.current_event.decoded_body))["Body"]
    guesses: list = list(filter(None, body.split("\n")))
    guesses.pop(0)  # get rid of header line

    try:
        assert len(guesses) > 0 and len(guesses) <= 6
        last_guess: str = guesses[-1]

        correct: list = len([m.start() for m in re.finditer(green, last_guess)])
        parial: list = len([m.start() for m in re.finditer(yellow, last_guess)])
        incorrect: list = len([m.start() for m in re.finditer(black, last_guess)])

        logger.debug(f"Correct: {correct}")
        logger.debug(f"Parial: {parial}")
        logger.debug(f"Incorrect: {incorrect}")
        assert correct + parial + incorrect == 5

        return Response(
            status_code=200,
            body=responses[len(guesses)] if correct == 5 else "Better luck tomorrow!",
            content_type="text/plain",
        )

    except Exception as e:
        logger.error(e)
        return Response(
            status_code=200,
            body="Invalid Wordle payload",
            content_type="text/plain",
        )


@app.get("/health")
def health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    return app.resolve(event, context)
