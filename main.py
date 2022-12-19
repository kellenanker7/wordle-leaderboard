import boto3

from helpers.config import Config

from twilio.twiml.messaging_response import MessagingResponse

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver


config = Config()
logger = Logger()
app = APIGatewayHttpResolver()

table = boto3.resource("dynamodb").Table(config.ddb_table)


@app.post("/post")
@authorize(app)
def post():
    resp = MessagingResponse()
    resp.message("The Robots are coming! Head for the hills!")

    return str(resp)


@app.get("/health")
def health() -> dict:
    return {"health": "alive"}


@app.exception_handler(Exception)
def handle_invalid_payload(e: Exception):
    raise


def api_handler(event: dict, context: LambdaContext) -> dict:
    logger.debug(event)
    app.resolve(event, context)
