"""Message sanitization utilities."""

import re


def clean_text(text: str, max_length: int = 2000) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_length]


def extract_hashtags(text: str) -> list:
    return re.findall(r"#(\w+)", text)


def extract_mentions(text: str) -> list:
    return re.findall(r"@(\w+)", text)


def is_greeting(text: str) -> bool:
    greetings = {
        "hi", "hello", "hey", "greetings", "good morning", "good afternoon",
        "good evening", "moni", "muli bwanji", "zikomo",
    }
    return text.lower().strip().rstrip("!?. ") in greetings


def is_question(text: str) -> bool:
    if "?" in text:
        return True
    starters = ("what", "how", "why", "when", "where", "who", "can", "could",
                 "would", "will", "do", "does", "is", "are")
    return text.lower().startswith(starters)
