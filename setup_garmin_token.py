#!/usr/bin/env python3
"""
Run locally, ONCE, to authenticate with Garmin (prompts for an MFA code if
2FA is enabled) and print the token blob you paste into the
/garmin-mcp/tokens SSM parameter. Nothing is uploaded anywhere by this
script -- you copy/paste the value yourself.

The Lambda saves refreshed tokens back to that parameter on its own, so
re-run this only if the session is revoked (e.g. you change your Garmin
password) or the server goes unused long enough for the refresh token
itself to lapse.

Usage:
    pip install garminconnect
    python setup_garmin_token.py
"""
import getpass

from garminconnect import Garmin

TOKEN_FILE = "./garmin_tokens.json"


def main():
    email = input("Garmin email: ")
    password = getpass.getpass("Garmin password: ")

    client = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Enter MFA code: "),
    )
    client.login(TOKEN_FILE)  # writes TOKEN_FILE; MFA prompt fires above if needed

    print("\nIn the AWS console, under Systems Manager > Parameter Store, create")
    print("(or edit) the SecureString parameter /garmin-mcp/tokens and paste this")
    print("whole JSON blob as its value:\n")
    print("--- /garmin-mcp/tokens ---")
    with open(TOKEN_FILE) as f:
        print(f.read().strip())


if __name__ == "__main__":
    main()
