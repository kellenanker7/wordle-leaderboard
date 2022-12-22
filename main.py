import boto3
import re
import time

from collections import defaultdict

from helpers.authorizer import authorize
from helpers.config import Config

from urllib.parse import parse_qsl

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


def sms_response(msg: str) -> Response:
    return Response(
        status_code=200,
        body=msg,
        content_type="text/plain",
    )


@app.post("/post")
@authorize(app)
def post_score() -> str:
    try:
        decoded_body: dict = dict(parse_qsl(app.current_event.decoded_body))
        logger.debug(decoded_body)

        from_number: int = int(decoded_body["From"][2:])
        guesses: list = list(filter(None, decoded_body["Body"].split("\n")))
        puzzle_number: int = int(guesses.pop(0).split(" ")[1])

        # Must have guessed between 1 and 6 times.
        assert len(guesses) >= 1 and len(guesses) <= 6

        # Each guess must be 5 letters
        for guess in guesses:
            correct: int = len([m.start() for m in re.finditer(green, guess)])
            assert (
                correct
                + len([m.start() for m in re.finditer(yellow, guess)])
                + len([m.start() for m in re.finditer(black, guess)])
                == 5
            )

        # Final guess must be all correct if fewer than 6 guesses.
        assert correct == 5 or len(guesses) == 6
        victory: int = correct == 5

    except:
        return sms_response(msg="Invalid Wordle payload")

    try:
        table.put_item(
            Item={
                "PhoneNumber": from_number,
                "PuzzleNumber": puzzle_number,
                "Guesses": len(guesses),
                "Victory": victory,
                "CreateTime": int(time.time() * 10**6),
            },
        )
        return sms_response(
            msg=responses[len(guesses)] if victory else "Better luck tomorrow!"
        )

    except Exception as e:
        logger.error(f"Error putting item: {e}")
        return sms_response(msg="Oh no! Something went wrong! Please try again.")


@app.get("/topten")
def get_top_ten() -> list:
    logger.debug(app.current_event.query_string_parameters)
    days = int(app.current_event.get_query_string_value("days", default_value=7))

    now = int(time.time() * 10**6)
    then = now - (days * 24 * 60 * 60 * 10**6)

    items: list = table.scan(
        FilterExpression="#CreateTime BETWEEN :then AND :now",
        ExpressionAttributeValues={
            ":then": then,
            ":now": now,
        },
        ExpressionAttributeNames={"#CreateTime": "CreateTime"},
        ReturnConsumedCapacity="NONE",
        ProjectionExpression="PhoneNumber,Guesses,Victory",
    )["Items"]

    guesses_by_number: dict = defaultdict(list)


@app.get("/user/<user>")
def get_top_ten(user: str) -> list:
    items: list = table.scan(
        FilterExpression="#PhoneNumber = :who",
        ExpressionAttributeValues={
            ":who": int(user),
        },
        ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
        ReturnConsumedCapacity="NONE",
        ProjectionExpression="PhoneNumber,PuzzleNumber,Guesses,Victory",
    )["Items"]

    return sorted(items, key=lambda x: x["PuzzleNumber"], reverse=True)


@app.get("/health")
def get_health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    return app.resolve(event, context)
