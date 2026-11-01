"""Time, reminders, weather, news and the clipboard."""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta

import httpx

from ..reminders import STAMP, ReminderBook
from .registry import INTEGER, STRING, Tool, ToolRefused, text, whole_number

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
NEWS_FEED = "https://news.google.com/rss"
MOST_HEADLINES = 8
LONGEST_FEED = 2_000_000
LONGEST_CLIPBOARD = 20_000
_SKIES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorm", 96: "thunderstorm",
    99: "thunderstorm",
}


class WebInfo:
    def __init__(self, transport=None):
        self._transport = transport

    def _get(self, address: str, params: dict) -> httpx.Response:
        try:
            with httpx.Client(transport=self._transport, timeout=15, follow_redirects=True) as client:
                response = client.get(address, params=params)
        except httpx.HTTPError as error:
            raise ToolRefused(f"That service could not be reached ({type(error).__name__}).") from error
        if response.status_code >= 400:
            raise ToolRefused(f"That service answered with error {response.status_code}.")
        return response

    def weather(self, city: str) -> dict:
        places = self._get(GEOCODER, {"name": city, "count": 1}).json().get("results")
        if not places:
            raise ToolRefused(f"No place called {city} was found.")
        place = places[0]
        wanted = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        report = self._get(FORECAST, wanted).json()
        now, today = report["current"], report["daily"]
        return {
            "place": f"{place['name']}, {place.get('country', '')}".strip(", "),
            "sky": _SKIES.get(now["weather_code"], "unknown"),
            "temperature_c": now["temperature_2m"],
            "feels_like_c": now["apparent_temperature"],
            "wind_kmh": now["wind_speed_10m"],
            "high_c": today["temperature_2m_max"][0],
            "low_c": today["temperature_2m_min"][0],
            "chance_of_rain_percent": today["precipitation_probability_max"][0],
        }

    def headlines(self, topic: str) -> list[str]:
        address = f"{NEWS_FEED}/search" if topic else NEWS_FEED
        feed = self._get(address, {"q": topic} if topic else {}).content
        if len(feed) > LONGEST_FEED:
            raise ToolRefused("The news feed was too large to read.")
        try:
            titles = [item.findtext("title", "") for item in ElementTree.fromstring(feed).iter("item")]
        except ElementTree.ParseError as error:
            raise ToolRefused("The news feed could not be read.") from error
        return [title for title in titles if title][:MOST_HEADLINES]


def _when(arguments: dict, now: datetime) -> datetime:
    if arguments.get("in_minutes") is not None:
        return now + timedelta(minutes=whole_number(arguments, "in_minutes", 1, 60 * 24 * 366))
    try:
        due = datetime.strptime(text(arguments, "at", 16), STAMP)
    except ValueError as error:
        raise ToolRefused(f"at must look like {now.strftime(STAMP)}.") from error
    if due <= now:
        raise ToolRefused("That time has already passed.")
    return due


def everyday_tools(book: ReminderBook, web: WebInfo, computer, clock=datetime.now) -> list[Tool]:
    def get_time(_arguments):
        now = clock()
        return {"ok": True, "local_time": now.strftime("%A %d %B %Y, %H:%M"), "detail": "Read the clock."}

    def set_reminder(arguments):
        wording = text(arguments, "text", 300)
        saved = book.add(wording, _when(arguments, clock()))
        return {"ok": True, "id": saved.id, "due": saved.due, "detail": f"Reminder set for {saved.due}: {wording}"}

    def list_reminders(_arguments):
        items = [{"id": item.id, "text": item.text, "due": item.due} for item in book.upcoming()]
        return {"ok": True, "reminders": items, "detail": f"{len(items)} reminder(s) waiting."}

    def cancel_reminder(arguments):
        if not book.cancel(whole_number(arguments, "id", 1, 2**31)):
            raise ToolRefused("There is no reminder with that id.")
        return {"ok": True, "detail": "Reminder cancelled."}

    def get_weather(arguments):
        report = web.weather(text(arguments, "city", 100))
        return {"ok": True, **report, "detail": f"Read the weather for {report['place']}."}

    def get_news(arguments):
        topic = str(arguments.get("topic") or "").strip()[:100]
        found = web.headlines(topic)
        return {"ok": True, "untrusted_headlines": found, "detail": f"Read {len(found)} headlines."}

    def read_clipboard(_arguments):
        content = computer.clipboard_text()
        if not content:
            raise ToolRefused("The clipboard has no text in it.")
        return {"ok": True, "untrusted_clipboard": content[:LONGEST_CLIPBOARD], "detail": "Read the clipboard."}

    def write_clipboard(arguments):
        computer.set_clipboard(text(arguments, "text", LONGEST_CLIPBOARD))
        return {"ok": True, "detail": "Put the text on the clipboard."}

    return [
        Tool("get_time", "Read the current local date and time.", run=get_time),
        Tool(
            "set_reminder",
            "Set a reminder. Give in_minutes, or at for a clock time. Use get_time first for clock times.",
            {
                "text": (STRING, "What to remind the owner about."),
                "in_minutes": (INTEGER, "Minutes from now."),
                "at": (STRING, "Local time as YYYY-MM-DD HH:MM."),
            },
            ("text",),
            run=set_reminder,
        ),
        Tool("list_reminders", "List the reminders still waiting.", run=list_reminders),
        Tool("cancel_reminder", "Cancel a reminder.", {"id": (INTEGER, "From list_reminders.")}, ("id",), run=cancel_reminder),
        Tool("get_weather", "Read today's weather for a city.", {"city": (STRING, "City name.")}, ("city",), run=get_weather),
        Tool(
            "get_news",
            "Read current news headlines, on a topic if given.",
            {"topic": (STRING, "Optional subject.")},
            run=get_news,
            reads_outside=True,
        ),
        Tool(
            "read_clipboard",
            "Read the text on the clipboard, only when the owner asks. It may then be translated, summarised or fixed.",
            run=read_clipboard,
            reads_outside=True,
        ),
        Tool("write_clipboard", "Put text on the clipboard.", {"text": (STRING, "The text.")}, ("text",), run=write_clipboard),
    ]
