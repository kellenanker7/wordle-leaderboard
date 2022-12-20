import boto3
import re

from urllib.parse import parse_qsl

from helpers.authorizer import authorize
from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.api_gateway import Response


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


def build_response(msg: str) -> Response:
    return Response(
        status_code=200,
        body=msg,
        content_type="text/plain",
    )


@app.post("/post")
@authorize(app)
def post_score() -> str:
    body: dict = dict(parse_qsl(app.current_event.decoded_body))["Body"]

    try:
        guesses: list = list(filter(None, body.split("\n")))
        guesses.pop(0)  # get rid of header line
        correct: int = 0

        # Must have guessed between 1 and 6 times.
        assert len(guesses) >= 1 and len(guesses) <= 6

        for guess in guesses:
            correct = len([m.start() for m in re.finditer(green, guess)])
            parial: int = len([m.start() for m in re.finditer(yellow, guess)])
            incorrect: int = len([m.start() for m in re.finditer(black, guess)])

            # Each guess must be 5 letters
            assert correct + parial + incorrect == 5

        # Final guess must be all correct if fewer than 6 guesses.
        assert correct == 5 or len(guesses) == 6

        return build_response(
            msg=responses[len(guesses)] if correct == 5 else "Better luck tomorrow!"
        )

    except:
        return build_response(msg="Invalid Wordle payload")


@app.get("/topten")
def get_top_ten() -> list:
    return [
        {
            "number": "(609)847-9282",
            "avg": "3",
        },
        {
            "number": "(505)363-2959",
            "avg": "3",
        },
    ]


@app.get("/health")
def get_health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    return app.resolve(event, context)
