#!/usr/bin/env python3
"""
Run locally, ONCE, to authenticate with Garmin (prompts for an MFA code if
2FA is enabled) and print the token blob you paste into the Lambda
console's environment variable. Nothing is uploaded anywhere by this
script -- you copy/paste the value yourself.

Garmin's tokens last ~1 year -- re-run this only when they expire or you
revoke the session (e.g. changing your Garmin password).

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

    print("\nIn the Lambda console, under Configuration > Environment variables,")
    print("add this (paste the whole JSON blob as the value):\n")
    print("--- GARMIN_TOKENS ---")
    with open(TOKEN_FILE) as f:
        print(f.read().strip())


if __name__ == "__main__":
    main()
