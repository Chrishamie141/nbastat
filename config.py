"""Centralized safe environment configuration for CLI and backend startup."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TRACKED_KEYS = ("THE_ODDS_API_KEY", "SPORTSDATAIO_API_KEY", "OPENWEATHER_API_KEY")


def get_config_status():
    return {key: "Loaded" if os.getenv(key) else "Missing" for key in TRACKED_KEYS}


def data_mode():
    status = get_config_status()
    loaded = sum(value == "Loaded" for value in status.values())
    if loaded == len(status):
        return "live"
    if loaded:
        return "partial_live"
    return "sample"


def print_config_status():
    status = get_config_status()
    print("--------------------------------")
    print("Configuration Status")
    print("--------------------------------")
    for key in TRACKED_KEYS:
        print(f"{key + ':':<26}{status[key]}")
    print("--------------------------------")
    if any(value == "Missing" for value in status.values()):
        print("Missing providers will use DEMO / SAMPLE DATA fallback where live data is unavailable.")
