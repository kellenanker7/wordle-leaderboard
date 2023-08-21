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

ddb = boto3.resource("dynamodb")
scores_table = ddb.Table(config.scores_table)
wordles_table = ddb.Table(config.wordles_table)
users_table = ddb.Table(config.users_table)
ip_utc_offset_table = ddb.Table(config.ip_utc_offset_table)

responses: dict = {
    1: "Hole in one!",
    2: "Albatross!",
    3: "Birdie!",
    4: "Par",
    5: "Bogey",
    6: "Double bogey",
}

err_msg_to_user = "Something went wrong! Please try again."
reminder_msg = "Don't forget to do today's Wordle!\n\nhttps://nytimes.com/games/wordle\n\nText ENOUGH to opt out of these reminders."
unsubscribed_msg = (
    "You will no longer receive daily Wordle reminders.\n\nText REMIND to opt back in."
)
subscribed_msg = (
    "Successfully subscribed to Wordle reminders!\n\nText ENOUGH to opt out."
)


def sms_response(msg: str) -> Response:
    return Response(
        status_code=200,
        body=msg,
        content_type="text/plain",
    )


def get_todays_wordle_number(ip: str = None, utc_offset: int = None) -> int:
    offset = utc_offset if utc_offset else int(get_user_utc_offset(ip=ip))
    hours_since_epoch = time.time() / 60 / 60 + offset

    return (
        int(hours_since_epoch / 24)
        - config.reference["days_since_epoch"]
        + config.reference["puzzle_number"]
    )


def get_user_utc_offset(ip: str) -> Decimal:
    try:
        return ip_utc_offset_table.query(
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

        raw_offset = int(response.json()["utc_offset"].replace(":", ""))
        sign = -1 if raw_offset < 0 else 1
        raw_offset = abs(raw_offset)

        utc_offset = Decimal((int(raw_offset / 100) + (raw_offset % 100) / 60.0) * sign)

        ip_utc_offset_table.put_item(
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

    cell = content.find_all("table")[0].find_all("tr")[0].find_all("td")
    answer = cell[2].get_text().strip()

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
    for u in users_table.scan(
        ProjectionExpression="PhoneNumber",
        FilterExpression="attribute_not_exists(Unsubscribed)",
    )["Items"]:
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
                body=reminder_msg,
                to=f"+1{u['PhoneNumber']}",
            )
            logger.info(f"Sent {message.sid}")


def unsubscribe_user(user: int) -> Response:
    try:
        users_table.update_item(
            Key={"PhoneNumber": user},
            UpdateExpression="SET #Unsubscribed = :val",
            ExpressionAttributeNames={"#Unsubscribed": "Unsubscribed"},
            ExpressionAttributeValues={":val": True},
        )
        return sms_response(msg=unsubscribed_msg)
    except Exception as e:
        logger.error(f"Error unsubscribing user {user}: {e}")
        return sms_response(msg=err_msg_to_user)


def subscribe_user(user: int) -> Response:
    try:
        users_table.update_item(
            Key={"PhoneNumber": user},
            UpdateExpression="REMOVE Unsubscribed",
        )
        return sms_response(msg=subscribed_msg)
    except Exception as e:
        logger.error(f"Error subscribing user {user}: {e}")
        return sms_response(msg=err_msg_to_user)


@app.post("/post")
@authorize(app)
def post_score() -> Response:
    try:
        decoded_body: dict = dict(parse_qsl(app.current_event.decoded_body))

        phone_number = int(decoded_body["From"][2:])
        first_line = list(filter(None, decoded_body["Body"].split("\n")))[0]

        chunks: list = first_line.split(" ")

        # Handle some other message types
        if chunks[0].lower() == "enough":
            return unsubscribe_user(user=phone_number)
        if chunks[0].lower() == "remind":
            return subscribe_user(user=phone_number)

        puzzle_number = int(chunks[1])

        try:
            guesses = int(chunks[2].split("/")[0])
            victory = True
        except ValueError:
            guesses = 6
            victory = False

        assert guesses >= 1 and guesses <= 6
    except Exception as e:
        logger.error(e)
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
        return sms_response(msg=err_msg_to_user)


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
        caller_name = users_table.query(
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
    todays_wordle = get_todays_wordle_number(
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

    answer = None
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
            caller_name = users_table.query(
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


def api_handler(event: dict, context: LambdaContext) -> str:
    if "updater" in event:
        get_todays_wordle_answer()
    elif "reminder" in event:
        send_reminders()
    else:
        return app.resolve(event, context)
