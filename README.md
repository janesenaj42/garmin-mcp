# garmin-mcp

MCP server exposing your own Garmin Connect data (steps, sleep, heart rate,
Body Battery, activities, daily stats, cycle tracking, weight)
as tools Claude can call, hosted as a single AWS Lambda function with a
public Function URL.

Your Garmin session tokens live in one SSM Parameter Store SecureString
(encrypted at rest by the AWS-managed `aws/ssm` key). Garmin rotates the
refresh token every time the access token is refreshed, so the Lambda
writes the new tokens back to that parameter whenever they change --
that's what keeps the session alive without you re-running the login
script. Free tier covers personal use (Lambda: 1M requests + 400k
GB-s/month; Function URLs: no extra charge; Parameter Store standard
parameters: free).

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

   In the AWS console, go to **Systems Manager > Parameter Store > Create
   parameter**:
   - Name: `/garmin-mcp/tokens`
   - Tier: Standard, Type: **SecureString**, KMS key: `alias/aws/ssm`
     (the default)
   - Value: paste the JSON blob

   Create it in the same region you'll create the Lambda in.

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
   - Configuration > Permissions > click the role name > Add permissions >
     Create inline policy > JSON. Clear the editor and paste the policy
     below, with `REGION` and `ACCOUNT_ID` replaced by your own values
     (e.g. `us-east-1` and your 12-digit account ID -- the whole
     placeholder, no `<>` left over). Paste only the `{ ... }` part, not
     the ```` ```json ```` fence lines. Save it as `garmin-mcp-tokens`:
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Action": ["ssm:GetParameter", "ssm:PutParameter"],
         "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/garmin-mcp/tokens"
       }]
     }
     ```
     "The policy failed legacy parsing" means the JSON didn't parse --
     usually a leftover placeholder, a pasted fence line, or curly quotes.
     If your account is in an AWS Organization, the editor may also show
     an `access-analyzer:ValidatePolicy ... explicit deny in a service
     control policy` error: that's only the console's policy linter being
     blocked, not the policy itself -- ignore it and click Next.
     No KMS permission is needed -- the default `aws/ssm` key already
     allows use through SSM by roles in your account.
   - Optional: Configuration > Concurrency > Reserved concurrency: `1`.
     Two containers refreshing at the same moment could otherwise save a
     token the other has already rotated out.

4. **Set environment variables** (Configuration > Environment variables)
   - `API_KEY` -- any random string, e.g. run `openssl rand -hex 16` locally

5. **Turn on a Function URL** (Configuration > Function URL > Create)
   - Auth type: `NONE`
   - Copy the URL it gives you, e.g. `https://abc123.lambda-url.us-east-1.on.aws/`

6. **Add the `ALLOWED_HOST` environment variable**
   - Back in Configuration > Environment variables, add `ALLOWED_HOST` set to
     just the hostname from step 5's URL (no `https://`, no trailing slash),
     e.g. `abc123.lambda-url.us-east-1.on.aws`
   - FastMCP rejects any request whose `Host` header isn't on this list (DNS
     rebinding protection) -- it's a second layer on top of the `X-Api-Key`
     header check, not a replacement for it.

7. **Test it before wiring it into Claude**
   ```
   curl -s -X POST "https://<your-function-url>mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -H "X-Api-Key: <your-API_KEY>" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   Expect a JSON-RPC response listing 8 tools. If you get `401`, the
   `X-Api-Key` header doesn't match the `API_KEY` env var. If you get
   `421`, the `ALLOWED_HOST` value doesn't match your Function URL's
   hostname. If you get a 5xx or timeout, check **Monitor > View CloudWatch
   logs** on the Lambda console for the actual error.

8. **Connect it to Claude**
   - claude.ai > Settings > Connectors > Add custom connector
   - URL: `<function-url>mcp` (your Function URL + `mcp`, e.g.
     `https://abc123.lambda-url.us-east-1.on.aws/mcp`) -- no secret in the
     URL itself
   - **Authentication: select "No sign-in"** ("for servers that use an API
     key instead of OAuth"). Claude auto-detects "Sign in now" by default
     for any server that returns a 401 to an unauthenticated probe, and in
     that mode Request headers are sent *alongside* an OAuth handshake, not
     instead of it -- this server has no OAuth support at all, so it'll
     fail to connect ("Couldn't register with garmin-mcp's sign-in
     service") until you switch this.
   - Under **Request headers**, add: name `X-Api-Key`, value
     `<your-API_KEY>` (not `Authorization` -- Claude blocks that name as
     reserved for OAuth)
   - Add

The Lambda keeps `/garmin-mcp/tokens` current by itself. If tools start
failing with `Failed to retrieve social profile` or other authentication
errors (e.g. you changed your Garmin password, or the server went unused
long enough for the refresh token to lapse), re-run step 1 and paste the
new blob over the parameter's value -- no redeploy needed.

### Migrating from the `GARMIN_TOKENS` env var

Earlier versions read tokens from a `GARMIN_TOKENS` environment variable.
To switch: re-run step 1 and create the parameter, add the IAM policy
from step 3, upload a freshly built `function.zip`, then delete the
`GARMIN_TOKENS` environment variable.
