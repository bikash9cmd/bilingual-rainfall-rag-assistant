"""The Lambda handler against mocked S3 (Barsha's bucket) and DynamoDB (the answer log)."""

import json

import boto3
import pytest
from moto import mock_aws

from .conftest import GOLD


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket="barsha-lake", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        for t in ("rain_daily", "monsoon_season", "station_dim"):
            s3.upload_file(f"{GOLD}/{t}/data.parquet", "barsha-lake", f"gold/{t}/data.parquet")
        ddb = boto3.client("dynamodb", region_name="ap-south-1")
        ddb.create_table(
            TableName="asks",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("SODHNUS_GOLD", "s3://barsha-lake/gold")
        monkeypatch.setenv("SODHNUS_BACKEND", "rules")
        monkeypatch.setenv("SODHNUS_LOG_TABLE", "asks")
        from sodhnus.aws import handlers

        monkeypatch.setattr(handlers, "_settings", None)
        yield handlers, boto3.resource("dynamodb", region_name="ap-south-1").Table("asks")


def _event(route, body=None):
    return {"routeKey": route, "body": json.dumps(body) if body is not None else None, "isBase64Encoded": False}


def test_ask_logs_a_redacted_record_and_emits_metrics(aws, capsys):
    handlers, table = aws
    q = "२०८१ असोज १२ गते काठमाडौंमा कति पानी पर्यो? मेरो नम्बर 9841234567"
    resp = handlers.api(_event("POST /ask", {"question": q}), None)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and "१९४.१" in body["answer"] and body["data_through"] == "2025-08-24"
    item = table.get_item(Key={"id": body["id"]})["Item"]
    assert "9841234567" not in item["question"] and "[phone]" in item["question"]
    assert item["checked"] is True and item["expires_at"] > item["ts"]
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"_aws"')]
    assert emf and emf[0]["Answers"] == 1 and emf[0]["Backend"] == "rules"
    assert emf[0]["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]


def test_devanagari_phone_number_is_redacted_in_the_log(aws):
    handlers, table = aws
    q = "२०८१ असोज १२ गते काठमाडौंमा कति पानी पर्यो? दाइको नम्बर ९८४१२३४५६७"
    body = json.loads(handlers.api(_event("POST /ask", {"question": q}), None)["body"])
    item = table.get_item(Key={"id": body["id"]})["Item"]
    assert "९८४१२३४५६७" not in item["question"] and "[phone]" in item["question"]
    assert "२०८१ असोज १२" in item["question"]


def test_feedback(aws):
    handlers, table = aws
    body = json.loads(handlers.api(_event("POST /ask", {"question": "Pokhara monsoon 2019"}), None)["body"])
    assert handlers.api(_event("POST /feedback", {"id": body["id"], "helpful": False}), None)["statusCode"] == 200
    assert table.get_item(Key={"id": body["id"]})["Item"]["helpful"] is False
    assert handlers.api(_event("POST /feedback", {"id": "nope", "helpful": True}), None)["statusCode"] == 404
    assert handlers.api(_event("POST /feedback", {"id": 3}), None)["statusCode"] == 400


def test_health_and_bad_requests(aws):
    handlers, _ = aws
    health = json.loads(handlers.api(_event("GET /health"), None)["body"])
    assert health["data_through"] == "2025-08-24" and health["data_age_days"] > 0
    assert handlers.api({"routeKey": "POST /ask", "body": "not json"}, None)["statusCode"] == 400
    assert handlers.api(_event("DELETE /ask"), None)["statusCode"] == 404
