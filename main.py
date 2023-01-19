import boto3
import re
import time
import requests
from twilio.rest import Client

from collections import defaultdict
from decimal import Decimal
from itertools import groupby
from operator import itemgetter

from helpers.authorizer import authorize
from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.api_gateway import Response

from bs4 import BeautifulSoup

from urllib.parse import parse_qsl

config = Config()
logger = Logger()
app = APIGatewayHttpResolver()

client = Client(config.twilio_account_sid, config.twilio_auth_token)

scores_table = boto3.resource("dynamodb").Table(config.scores_table)
wordles_table = boto3.resource("dynamodb").Table(config.wordles_table)
users_table = boto3.resource("dynamodb").Table(config.users_table)
ip_utc_offset = boto3.resource("dynamodb").Table(config.ip_utc_offset_table)

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


def get_todays_wordle_number(ip: str = None, utc_offset: int = None) -> int:
    offset: int = utc_offset if utc_offset else int(get_user_utc_offset(ip=ip))
    hours_since_epoch: int = time.time() / 60 / 60 + offset

    return (
        int(hours_since_epoch / 24)
        - config.reference["days_since_epoch"]
        + config.reference["puzzle_number"]
    )


def get_user_utc_offset(ip: str) -> Decimal:
    try:
        return ip_utc_offset.query(
            ProjectionExpression="UtcOffest",
            KeyConditionExpression="#IpAddress = :val",
            ExpressionAttributeNames={"#IpAddress": "IpAddress"},
            ExpressionAttributeValues={":val": ip},
            Limit=1,
        )["Items"][0]["UtcOffest"]

    except (KeyError, IndexError):
        logger.info(f"Cache miss: {ip}")

        response: requests.Response = requests.get(f"{config.tz_api}{ip}")
        response.raise_for_status()

        raw_offset: int = int(response.json()["utc_offset"].replace(":", ""))
        sign = -1 if raw_offset < 0 else 1
        raw_offset = abs(raw_offset)

        utc_offset: Decimal = Decimal(
            (int(raw_offset / 100) + (raw_offset % 100) / 60.0) * sign
        )

        ip_utc_offset.put_item(
            Item={
                "IpAddress": ip,
                "UtcOffest": utc_offset,
            },
        )

        return utc_offset


def get_todays_wordle_answer() -> None:
    content = BeautifulSoup(
        requests.get(config.wordle_archive_api).text,
        features="html.parser",
    ).select("section.content")[0]

    cell: str = content.find_all("table")[0].find_all("tr")[0].find_all("td")
    answer: str = cell[2].get_text().strip()

    try:
        definitions: list = []
        response: requests.Response = requests.get(f"{config.dictionary_api}{answer}")
        response.raise_for_status()

        for i in response.json():
            for m in i["meanings"]:
                definitions.append(
                    {
                        "part_of_speech": m["partOfSpeech"],
                        "definitions": [d["definition"] for d in m["definitions"]],
                    }
                )
    except Exception as e:
        logger.warn(f"Definition(s) not found for {answer}")
        definitions: list = [{"part_of_speech": "", "definitions": []}]

    wordles_table.put_item(
        Item={
            "Id": int(cell[1].get_text().strip()),
            "Answer": answer,
            "Definitions": definitions,
        },
    )


def send_reminders() -> None:
    for u in users_table.scan(ProjectionExpression="PhoneNumber")["Items"]:
        if (
            len(
                scores_table.query(
                    KeyConditionExpression="#PuzzleNumber = :puzzle AND #PhoneNumber = :who",
                    ExpressionAttributeNames={
                        "#PuzzleNumber": "PuzzleNumber",
                        "#PhoneNumber": "PhoneNumber",
                    },
                    ExpressionAttributeValues={
                        ":puzzle": get_todays_wordle_number(utc_offset=-5),
                        ":who": u["PhoneNumber"],
                    },
                    Limit=1,
                )["Items"]
            )
            == 0
        ):
            logger.info(f"Sending reminder to {u['PhoneNumber']}")
            message = client.messages.create(
                from_=config.twilio_messaging_service_sid,
                body="Don't forget to do today's Wordle! Text your score to this number and compete against your friends!\n\nnytimes.com/games/wordle",
                to=f"+1{u['PhoneNumber']}",
            )
            logger.debug(f"Sent {message.sid}")


@app.post("/post")
@authorize(app)
def post_score() -> str:
    try:
        decoded_body: dict = dict(parse_qsl(app.current_event.decoded_body))

        phone_number: int = int(decoded_body["From"][2:])
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
        scores_table.put_item(
            Item={
                "PhoneNumber": phone_number,
                "PuzzleNumber": puzzle_number,
                "Guesses": guesses,
                "Victory": victory,
                "CreateTime": int(time.time()),
            },
        )

        try:
            users_table.query(
                ProjectionExpression="CallerName",
                KeyConditionExpression="#PhoneNumber = :val",
                ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
                ExpressionAttributeValues={":val": phone_number},
                Limit=1,
            )["Items"][0]["CallerName"]
        except (KeyError, IndexError):
            logger.info(f"Cache miss: {phone_number}")
            users_table.put_item(
                Item={
                    "PhoneNumber": phone_number,
                    "CallerName": dict(
                        client.lookups.v2.phone_numbers(phone_number)
                        .fetch(fields="caller_name")
                        .caller_name
                    )["caller_name"],
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
    return sorted(
        [
            user(user=u)
            for u in set(
                [
                    i["PhoneNumber"]
                    for i in scores_table.scan(ProjectionExpression="PhoneNumber")[
                        "Items"
                    ]
                ]
            )
        ],
        key=lambda x: x["Average"],
    )


@app.get("/users")
def users() -> list:
    return sorted(users_table.scan()["Items"], key=lambda x: x["CallerName"])


@app.get("/user/<user>")
def user(user: str) -> dict:
    items: list = scores_table.scan(
        FilterExpression="#PhoneNumber = :who",
        ExpressionAttributeValues={":who": int(user)},
        ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
        ProjectionExpression="Guesses,Victory,PuzzleNumber",
    )["Items"]

    wins: list = [int(i["PuzzleNumber"]) for i in items if i["Victory"]]

    # https://stackoverflow.com/questions/2361945/detecting-consecutive-integers-in-a-list
    streaks: list = []
    for _, g in groupby(
        enumerate(wins),
        lambda ix: ix[0] - ix[1],
    ):
        streaks.append(list(map(itemgetter(1), g)))

    # Shouldn't be needed once everyone texts in again
    try:
        caller_name: str = users_table.query(
            ProjectionExpression="CallerName",
            KeyConditionExpression="#PhoneNumber = :val",
            ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
            ExpressionAttributeValues={":val": int(user)},
            Limit=1,
        )["Items"][0]["CallerName"]
    except:
        caller_name = None

    return {
        "CallerName": caller_name,
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
            < get_todays_wordle_number(app.current_event.request_context.http.source_ip)
            - 1
            else (len(streaks[-1]) if wins[-1] == items[-1]["PuzzleNumber"] else 0)
        ),
    }


@app.get("/today")
def today() -> dict:
    return {
        "PuzzleNumber": get_todays_wordle_number(
            ip=app.current_event.request_context.http.source_ip
        )
    }


@app.get("/wordles")
def users() -> list:
    todays_wordle: int = get_todays_wordle_number(
        ip=app.current_event.request_context.http.source_ip
    )
    return sorted(
        [i for i in wordles_table.scan()["Items"] if i["Id"] < todays_wordle],
        key=lambda x: x["Id"],
        reverse=True,
    )


@app.get("/wordle/<wordle>")
def wordle(wordle: str) -> dict:
    items: list = scores_table.scan(
        FilterExpression="#PuzzleNumber = :puzzle",
        ExpressionAttributeValues={
            ":puzzle": int(wordle),
        },
        ExpressionAttributeNames={"#PuzzleNumber": "PuzzleNumber"},
        ProjectionExpression="PhoneNumber,Guesses,Victory",
    )["Items"]

    answer: str = None
    definitions: list = []
    try:
        if int(wordle) < get_todays_wordle_number(
            ip=app.current_event.request_context.http.source_ip
        ):
            item: dict = wordles_table.query(
                KeyConditionExpression="#Id = :val",
                ExpressionAttributeNames={"#Id": "Id"},
                ExpressionAttributeValues={":val": int(wordle)},
                Limit=1,
            )["Items"][0]
            answer = item["Answer"]

            # Until all wordle items have been updated
            try:
                definitions = item["Definitions"]
            except:
                pass
    except (KeyError, IndexError):
        pass

    participants: list = []
    for i in items:
        try:
            caller_name: str = users_table.query(
                ProjectionExpression="CallerName",
                KeyConditionExpression="#PhoneNumber = :val",
                ExpressionAttributeNames={"#PhoneNumber": "PhoneNumber"},
                ExpressionAttributeValues={":val": i["PhoneNumber"]},
                Limit=1,
            )["Items"][0]["CallerName"]
        except:
            caller_name = None

        participants.append(
            {
                "CallerName": caller_name,
                "PhoneNumber": i["PhoneNumber"],
                "Guesses": int(i["Guesses"]),
                "Victory": i["Victory"],
            }
        )

    return {
        "PuzzleNumber": int(wordle),
        "Answer": answer,
        "Definitions": definitions,
        "Users": sorted(participants, key=lambda x: x["Guesses"]),
    }


@app.get("/health")
def health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    if "warmer" in event:
        return True
    elif "updater" in event:
        return get_todays_wordle_answer()
    elif "reminder" in event:
        return send_reminders()
    else:
        return app.resolve(event, context)
