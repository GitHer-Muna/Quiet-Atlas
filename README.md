# The Quiet Atlas

The Quiet Atlas is a small AWS application that keeps an almanac of real, quiet places. Once a day, a scheduled Lambda selects a populated place with fewer than 5,000 residents, retrieves its current weather, and asks an enabled Amazon Bedrock model to write a 150–220 word entry in the voice of a fictional keeper.

Visitors can also request an entry for a named place. When the place is found and its weather is available, the generated page is added to the permanent atlas. Each entry stores the source facts alongside the writing so the weather, coordinates, population, and fictional voice remain clearly distinguishable.

## Architecture

```text
EventBridge Scheduler
        |
        v
Daily Keeper Lambda ---- Open-Meteo Geocoding ---- Open-Meteo Weather
        |
        +---------------- Bedrock
        |
        v
AtlasEntries DynamoDB <---- List Entries Lambda <---- GET /entries

Browser -> S3 static website -> HTTP API
                                  |
                                  +--> On-Demand Keeper Lambda
                                          |  Open-Meteo Geocoding -> Weather -> Bedrock
                                          |  RequestThrottle DynamoDB
                                          +--> AtlasEntries DynamoDB
```

AWS SAM provisions the application infrastructure:

- Amazon EventBridge Scheduler invokes the daily keeper once per day.
- Three Python 3.12 AWS Lambda functions handle daily generation, on-demand requests, and listing entries.
- Amazon API Gateway provides `GET /entries` and `POST /entries/request`.
- Amazon DynamoDB stores entries and maintains the per-IP daily request counter.
- Amazon Bedrock generates the keeper prose.
- Amazon S3 hosts the static frontend.

Lambda permissions are limited to the required DynamoDB tables and the Bedrock model or inference profile configured during deployment.

## Prerequisites

- AWS CLI configured for the target account and region.
- AWS SAM CLI.
- Python 3.12.
- Bedrock model access enabled in the deployment region and account.
- An enabled Bedrock foundation-model ARN or cross-region inference-profile ARN. Some newer models require an inference profile rather than a bare model ID.

Open-Meteo provides both geocoding and weather without an API key or separate account. The examples use `us-east-1`; choose one region deliberately and keep the Lambda, DynamoDB, API, and Bedrock access aligned with that choice.

## Deploy

From the project root:

```bash
sam build
sam deploy --guided \
  --stack-name quiet-atlas \
  --region us-east-1 \
  --parameter-overrides \
    BedrockModelArn=YOUR_ENABLED_BEDROCK_MODEL_OR_PROFILE_ARN
```

The default request limit is five on-demand requests per visitor IP per UTC day. The API allows cross-origin requests because the static website and API use separate origins. Restrict `AllowOrigins` in `template.yaml` if the site is placed behind a known domain.

After deployment, publish the frontend and inject the public API URL:

```bash
chmod +x scripts/deploy_frontend.sh
AWS_REGION=us-east-1 scripts/deploy_frontend.sh quiet-atlas
```

The stack outputs include the API URL, frontend bucket name, and S3 website URL. The deployment script writes only the public API URL into the frontend configuration. It does not place AWS credentials or Bedrock configuration in browser code.

## Test the deployed application

List entries:

```bash
API_URL="$(aws cloudformation describe-stacks --stack-name quiet-atlas --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text)"
curl -sS "$API_URL/entries"
```

Request an entry:

```bash
curl -sS -X POST "$API_URL/entries/request" \
  -H 'content-type: application/json' \
  -d '{"placeName":"Shiroka Laka, Bulgaria"}'
```

Run the daily keeper manually:

```bash
aws lambda invoke \
  --function-name quiet-atlas-daily-keeper-quiet-atlas \
  --region us-east-1 \
  --payload '{}' \
  /tmp/quiet-atlas-daily.json
cat /tmp/quiet-atlas-daily.json
```

The exact function name includes the stack name. Use `aws lambda list-functions` if a different stack name was selected.

## Local checks

Provider calls and AWS clients are mocked in the tests, so local validation does not require AWS credentials or Bedrock usage:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

To preview the frontend without a deployed API:

```bash
python3 -m http.server 8080 --directory frontend
```

The page displays a connection message until its generated API configuration contains a URL.

## Operational notes

- Open-Meteo geocoding and weather requests retry once before returning a friendly application error.
- The daily function logs expected failures and does not write a fabricated entry when a provider or model is unavailable.
- The on-demand limit uses a salted SHA-256 hash of API Gateway's source IP and the UTC date. The raw IP is never stored. DynamoDB TTL removes old counters after two days.
- The S3 bucket is public because it serves the simple static website. Do not place secrets or private files in it. A production deployment should put the bucket behind CloudFront and keep it private.
- DynamoDB uses on-demand billing. Monitor Bedrock usage and keep the request limit enabled before sharing the request form widely.
