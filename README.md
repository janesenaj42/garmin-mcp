# garmin-mcp

MCP server exposing your own Garmin Connect data (steps, sleep, heart rate,
Body Battery, activities, daily stats) as tools Claude can call, hosted as
a single AWS Lambda function with a public Function URL.

No S3, no SSM, no IAM policy authoring -- your Garmin session tokens live
only as a Lambda environment variable (encrypted at rest by Lambda's
default AWS-managed key). Free tier covers personal use (Lambda: 1M
requests + 400k GB-s/month; Function URLs: no extra charge).

## Prerequisites

- An AWS account
- [`uv`](https://docs.astral.sh/uv/) -- used to build the deployment zip
  without needing a system Python/pip install
- Docker (or a local Python 3.12+), to run `setup_garmin_token.py` --
  see step 1

## Setup

1. **Get your token locally**

   This script asks for your Garmin email/password (and MFA code, if you
   have 2FA) interactively -- run it yourself, in your own terminal, so
   your password never passes through anything else. If you don't have
   Python set up locally, Docker works too:
   ```
   docker run --rm -it -v "${PWD}:/app" -w /app python:3.12 \
     bash -c "pip install garminconnect==0.3.16 && python setup_garmin_token.py"
   ```
   or plain Python:
   ```
   pip install garminconnect==0.3.16
   python setup_garmin_token.py
   ```
   Either way, it logs into Garmin and prints one JSON blob (also saved
   locally to `garmin_tokens.json`, which `.gitignore` already excludes).
   Keep it handy -- you'll paste it in step 4.

2. **Build the deployment package**
   ```
   bash build.sh
   ```
   Produces `function.zip`, built for Python 3.12 / x86_64 to match the
   Lambda runtime in step 3 -- a mismatch there will fail to import at
   runtime.

3. **Create the Lambda function** (AWS Console > Lambda > Create function)
   - Author from scratch, name it `garmin-mcp`
   - Runtime: Python 3.12, Architecture: x86_64
   - Leave "Create a new role with basic Lambda permissions" selected
   - After it's created: Code > Upload from > .zip file > `function.zip`
   - Runtime settings > Handler: `lambda_function.handler`
   - Configuration > General configuration > Timeout: 30 sec

4. **Set environment variables** (Configuration > Environment variables)
   - `GARMIN_TOKENS` -- paste the JSON blob from step 1
   - `API_KEY` -- any random string, e.g. run `openssl rand -hex 16` locally

5. **Turn on a Function URL** (Configuration > Function URL > Create)
   - Auth type: `NONE`
   - Copy the URL it gives you, e.g. `https://abc123.lambda-url.us-east-1.on.aws/`

6. **Add the `ALLOWED_HOST` environment variable**
   - Back in Configuration > Environment variables, add `ALLOWED_HOST` set to
     just the hostname from step 5's URL (no `https://`, no trailing slash),
     e.g. `abc123.lambda-url.us-east-1.on.aws`
   - FastMCP rejects any request whose `Host` header isn't on this list (DNS
     rebinding protection) -- it's a second layer on top of the `API_KEY`
     check, not a replacement for it.

7. **Test it before wiring it into Claude**
   ```
   curl -s -X POST "https://<your-function-url>/<your-API_KEY>/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   Expect a JSON-RPC response listing 6 tools. If you get `401`, the
   API_KEY in the URL doesn't match the env var. If you get `421`, the
   `ALLOWED_HOST` value doesn't match your Function URL's hostname. If you
   get a 5xx or timeout, check **Monitor > View CloudWatch logs** on the
   Lambda console for the actual error.

8. **Connect it to Claude**
   - claude.ai > Settings > Connectors > Add custom connector
   - URL: `<function-url><API_KEY>/mcp` (your Function URL + API_KEY + `/mcp`,
     e.g. `https://abc123.lambda-url.us-east-1.on.aws/a1b2c3.../mcp`)
   - Leave OAuth fields blank > Add

Garmin's tokens last ~1 year; re-run step 1 and update `GARMIN_TOKENS`
when they expire (garminconnect will start raising authentication errors,
visible in CloudWatch logs, once that happens).

## Putting this in your own GitHub repo

This folder is a ready-to-push repo. On GitHub, create a new **empty**
repository (no README/license), then from this folder:

```
git init
git add .
git commit -m "garmin-mcp: initial version"
git branch -M main
git remote add origin git@github.com:<you>/garmin-mcp.git
git push -u origin main
```

`.gitignore` already excludes `build/`, `function.zip`, and
`garmin_tokens.json` so you never accidentally commit your tokens.
