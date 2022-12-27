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


config = Config()
logger = Logger()
app = APIGatewayHttpResolver()

scores = boto3.resource("dynamodb").Table(config.scores_table)

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
    response: requests.Response = requests.get(f"https://worldtimeapi.org/api/ip/{ip}")
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
    logger.debug(app.current_event.request_context.http.source_ip)
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
        ProjectionExpression="PhoneNumber,Guesses,Victory,PuzzleNumber",
        FilterExpression="#PuzzleNumber >= :then",
        ExpressionAttributeNames={"#PuzzleNumber": "PuzzleNumber"},
        ExpressionAttributeValues={":then": then},
    )["Items"]

    guesses_by_user: dict = defaultdict(list)
    wins_by_user: dict = defaultdict(list)

    for i in items:
        user: int = int(i["PhoneNumber"])
        guesses_by_user[user].append(int(i["Guesses"]))

        if i["Victory"]:
            wins_by_user[user].append(int(i["PuzzleNumber"]))

    # https://stackoverflow.com/questions/2361945/detecting-consecutive-integers-in-a-list
    streaks_by_user: dict = defaultdict(list)
    for k, v in wins_by_user.items():
        for _, g in groupby(
            enumerate(v),
            lambda ix: ix[0] - ix[1],
        ):
            streaks_by_user[k].append(list(map(itemgetter(1), g)))

    leaderboard: list = []
    for k, v in guesses_by_user.items():
        leaderboard.append(
            {
                "PhoneNumber": k,
                "Average": round(sum(v) / len(v), 2),
                "WinPercentage": round(len(wins_by_user[k]) / len(v) * 100, 2),
                "CurrentStreak": len(streaks_by_user[k][-1]),
            }
        )

    return sorted(leaderboard, key=lambda x: x["Average"])


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
def user(user: str) -> dict:
    items: list = scores.scan(
        FilterExpression="#PhoneNumber = :who",
        ExpressionAttributeValues={
            ":who": int(user),
        },
        ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
        ProjectionExpression="PuzzleNumber,Guesses,Victory",
    )["Items"]

    # https://stackoverflow.com/questions/2361945/detecting-consecutive-integers-in-a-list
    streaks: list = []
    for _, g in groupby(
        enumerate([i["PuzzleNumber"] for i in items if i["Victory"]]),
        lambda ix: ix[0] - ix[1],
    ):
        streaks.append(list(map(itemgetter(1), g)))

    return {
        "Puzzles": sorted(items, key=lambda x: x["PuzzleNumber"], reverse=True),
        "LongestStreak": sorted([len(i) for i in streaks])[-1],
        "CurrentStreak": len(streaks[-1]),
    }


@app.get("/today")
def today() -> dict:
    logger.debug(app.current_event.request_context.http.source_ip)
    return {
        "PuzzleNumber": get_todays_puzzle_number(
            ip=app.current_event.request_context.http.source_ip
        )
    }


@app.get("/puzzle/<puzzle>")
def puzzle(puzzle: str) -> dict:
    items: list = scores.scan(
        FilterExpression="#PuzzleNumber = :puzzle",
        ExpressionAttributeValues={
            ":puzzle": int(puzzle),
        },
        ExpressionAttributeNames={"#PuzzleNumber": "PuzzleNumber"},
        ProjectionExpression="PhoneNumber,Guesses,Victory",
    )["Items"]

    return {
        "PuzzleNumber": puzzle,
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
    return app.resolve(event, context)
