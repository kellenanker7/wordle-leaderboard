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
    2: "Albatross!",
    3: "Birdie!",
    4: "Par",
    5: "Bogey",
    6: "Double bogey",
}


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

        who: int = int(decoded_body["From"][2:])
        first_line: str = list(filter(None, decoded_body["Body"].split("\n")))[0]

        chunks: list = first_line.split(" ")
        puzzle_number: int = int(chunks[1])

        try:
            guesses: str = int(chunks[2].split("/")[0])
            victory: int = True
        except ValueError:
            guesses: int = 6
            victory: int = False

    except:
        return sms_response(msg="Invalid Wordle payload")

    try:
        table.put_item(
            Item={
                "PhoneNumber": who,
                "PuzzleNumber": puzzle_number,
                "Guesses": guesses,
                "Victory": victory,
                "CreateTime": int(time.time() * 10**6),
            },
        )
        return sms_response(
            msg=responses[guesses] if victory else "Better luck tomorrow!"
        )

    except Exception as e:
        logger.error(f"Error putting item: {e}")
        return sms_response(msg="Oh no! Something went wrong! Please try again.")


@app.get("/leaderboard")
def leaderboard() -> dict:
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
        ProjectionExpression="PhoneNumber,Guesses",
    )["Items"]

    guesses_by_user: dict = defaultdict(list)
    for i in items:
        guesses_by_user[int(i["PhoneNumber"])].append(int(i["Guesses"]))

    leaderboard: list = []
    for k, v in guesses_by_user.items():
        leaderboard.append({"PhoneNumber": k, "Average": round(sum(v) / len(v), 3)})

    return sorted(leaderboard, key=lambda x: x["Average"])


@app.get("/user/<user>")
def user(user: str) -> list:
    items: list = table.scan(
        FilterExpression="#PhoneNumber = :who",
        ExpressionAttributeValues={
            ":who": int(user),
        },
        ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
        ReturnConsumedCapacity="NONE",
        ProjectionExpression="PuzzleNumber,Guesses,Victory",
    )["Items"]

    return sorted(items, key=lambda x: x["PuzzleNumber"], reverse=True)


@app.get("/health")
def get_health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    return app.resolve(event, context)
