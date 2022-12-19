import boto3

from helpers.authorizer import authorize
from helpers.config import Config

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.api_gateway import Response


config = Config()
logger = Logger()
app = APIGatewayHttpResolver(debug=True)

table = boto3.resource("dynamodb").Table(config.ddb_table)


@app.post("/post")
@authorize(app)
def post() -> str:
    return Response(
        status_code=200,
        body="Hi Jill :)",
        content_type="text/plain",
    )


@app.get("/health")
def health() -> str:
    return {"status": "alive"}


def api_handler(event: dict, context: LambdaContext) -> str:
    logger.debug(event)
    return app.resolve(event, context)
