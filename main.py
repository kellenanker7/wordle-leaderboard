import boto3
import re
import time
import requests

from collections import defaultdict
from itertools import groupby
from operator import itemgetter

from helpers.authorizer import authorize
from helpers.config import Config

from urllib.parse import parse_qsl

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.api_gateway import Response

from bs4 import BeautifulSoup

config = Config()
logger = Logger()
app = APIGatewayHttpResolver()

scores = boto3.resource("dynamodb").Table(config.scores_table)
wordles = boto3.resource("dynamodb").Table(config.wordles_table)

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


def get_todays_puzzle_number(ip: str) -> int:
    offset: int = get_user_utc_offset(ip=ip)
    hours_since_epoch: int = time.time() / 60 / 60 + offset

    return (
        int(hours_since_epoch / 24)
        - config.reference["days_since_epoch"]
        + config.reference["puzzle_number"]
    )


def get_user_utc_offset(ip: str) -> int:
    response: requests.Response = requests.get(f"{config.tz_api}{ip}")
    response.raise_for_status()

    raw_offset: int = int(response.json()["utc_offset"].replace(":", ""))
    sign = -1 if raw_offset < 0 else 1
    raw_offset = abs(raw_offset)

    return (int(raw_offset / 100) + (raw_offset % 100) / 60.0) * sign


@app.post("/post")
@authorize(app)
def post_score() -> str:
    try:
        decoded_body: dict = dict(parse_qsl(app.current_event.decoded_body))

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

        assert guesses >= 1 and guesses <= 6
    except:
        return sms_response(msg="Invalid Wordle payload")

    try:
        scores.put_item(
            Item={
                "PhoneNumber": who,
                "PuzzleNumber": puzzle_number,
                "Guesses": guesses,
                "Victory": victory,
                "CreateTime": int(time.time()),
            },
        )
        return sms_response(
            msg=(responses[guesses] if victory else "Better luck tomorrow!")
            + "\n\nwordle.kellenanker.com"
        )

    except Exception as e:
        logger.error(f"Error putting item: {e}")
        return sms_response(msg="Oh no! Something went wrong! Please try again.")


@app.get("/leaderboard")
def leaderboard() -> list:
    logger.debug(app.current_event.query_string_parameters)

    limit = int(app.current_event.get_query_string_value("limit", default_value=7))
    then: int = (
        0
        if limit == 0
        else get_todays_puzzle_number(
            ip=app.current_event.request_context.http.source_ip
        )
        - limit
    )

    items: list = scores.scan(
        ProjectionExpression="PhoneNumber",
        FilterExpression="#PuzzleNumber >= :then",
        ExpressionAttributeNames={"#PuzzleNumber": "PuzzleNumber"},
        ExpressionAttributeValues={":then": then},
    )["Items"]

    return sorted(
        [
            x
            for x in [
                user(user=u, leaderboard=True)
                for u in set([i["PhoneNumber"] for i in items])
            ]
            if x
        ],
        key=lambda x: x["Average"],
    )


@app.get("/users")
def users() -> list:
    return list(
        sorted(
            set(
                [
                    int(i["PhoneNumber"])
                    for i in scores.scan(ProjectionExpression="PhoneNumber")["Items"]
                ]
            ),
            key=int,
        )
    )


@app.get("/user/<user>")
def user(user: str = "", leaderboard: bool = False) -> dict:
    items: list = scores.scan(
        FilterExpression="#PhoneNumber = :who",
        ExpressionAttributeValues={
            ":who": int(user),
        },
        ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
        ProjectionExpression="PhoneNumber,Guesses,Victory,PuzzleNumber",
    )["Items"]

    wins: list = [int(i["PuzzleNumber"]) for i in items if i["Victory"]]
    if leaderboard and len(wins) <= 3:
        return None

    # https://stackoverflow.com/questions/2361945/detecting-consecutive-integers-in-a-list
    streaks: list = []
    for _, g in groupby(
        enumerate(wins),
        lambda ix: ix[0] - ix[1],
    ):
        streaks.append(list(map(itemgetter(1), g)))

    return {
        "PhoneNumber": int(user),
        "Puzzles": sorted(items, key=lambda x: x["PuzzleNumber"], reverse=True),
        "Wins": sorted(wins, key=int, reverse=True),
        "WinPercentage": 0
        if len(items) < 1
        else round((len(wins) / len(items)) * 100, 2),
        "Average": 0
        if len(items) < 1
        else round(sum([int(i["Guesses"]) for i in items]) / len(items), 2),
        "LongestStreak": 0
        if len(streaks) < 1
        else sorted([len(s) for s in streaks])[-1],
        "CurrentStreak": (
            0
            if len(items) < 1
            or len(wins) < 1
            or wins[-1]
            < get_todays_puzzle_number(app.current_event.request_context.http.source_ip)
            - 1
            else (len(streaks[-1]) if wins[-1] == items[-1]["PuzzleNumber"] else 0)
        ),
    }


@app.get("/today")
def today() -> dict:
    return {
        "PuzzleNumber": get_todays_puzzle_number(
            ip=app.current_event.request_context.http.source_ip
        )
    }


@app.get("/wordles")
def users() -> list:
    return sorted(
        [i for i in wordles.scan()["Items"]],
        key=lambda x: x["Id"],
        reverse=True,
    ) + [get_todays_puzzle_number(ip=app.current_event.request_context.http.source_ip)]


@app.get("/wordle/<wordle>")
def wordle(wordle: str) -> dict:
    items: list = scores.scan(
        FilterExpression="#PuzzleNumber = :puzzle",
        ExpressionAttributeValues={
            ":puzzle": int(wordle),
        },
        ExpressionAttributeNames={"#PuzzleNumber": "PuzzleNumber"},
        ProjectionExpression="PhoneNumber,Guesses,Victory",
    )["Items"]

    answer: str = None
    try:
        if int(wordle) < get_todays_puzzle_number(
            ip=app.current_event.request_context.http.source_ip
        ):
            answer: str = (
                wordles.query(
                    KeyConditionExpression="#Id = :val",
                    ExpressionAttributeNames={"#Id": "Id"},
                    ExpressionAttributeValues={":val": int(wordle)},
                    Limit=1,
                )["Items"][0]["Answer"],
            )
    except IndexError:
        pass

    return {
        "PuzzleNumber": wordle,
        "Answer": answer,
        "Users": sorted(
            [
                {
                    "PhoneNumber": i["PhoneNumber"],
                    "Guesses": int(i["Guesses"]),
                    "Victory": i["Victory"],
                }
                for i in items
            ],
            key=lambda x: x["Guesses"],
        ),
    }


@app.get("/health")
def get_health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    if "warmer" in event:
        return True
    elif "updater" in event:
        return update_wordle_answers()
    else:
        return app.resolve(event, context)
