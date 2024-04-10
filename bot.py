import re
import tomllib
import logging
from os import getenv
from enum import StrEnum
import requests
from telegram import Update
from dotenv import load_dotenv
from spellchecker import SpellChecker
import telegram.ext.filters as filters
from telegram.constants import ParseMode
from jinja2 import FileSystemLoader, Environment
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    Defaults,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = getenv("BOT_TOKEN")
DEFINE_REGEX = re.compile(r"define (?P<word>[a-z]+)", re.I)

with open("replies.toml", "rb") as f:
    reply_book = tomllib.load(f)

jenv = Environment(trim_blocks=True, loader=FileSystemLoader("reply-templates/"))
reply_define_template = jenv.get_template("define.jinja2")
invalid_word_template = jenv.get_template("invalid-word.jinja2")
spell_checker = SpellChecker()


class MSGS(StrEnum):
    START_MSG = "START_MSG"
    HELP_MSG = "HELP_MSG"
    GENERAL_ERROR_MSG = "GENERAL_ERROR_MSG"
    SYNTAX_ERROR_MSG = "SYNTAX_ERROR_MSG"


class Definition:
    def __init__(self, json: dict) -> None:
        self.definition = json["definition"]

        self.synonyms = None
        if json["synonyms"]:
            self.synonyms = json["synonyms"]

        self.antonyms = None
        if json["antonyms"]:
            self.antonyms = json["antonyms"]

        self.example = None
        if "example" in json:
            self.example = json["example"]


class Meaning:
    def __init__(self, json: dict) -> None:
        self.part_of_speech = json["partOfSpeech"]

        self.synonyms = None
        if json["synonyms"]:
            self.synonyms = json["synonyms"]

        self.antonyms = None
        if json["antonyms"]:
            self.antonyms = json["antonyms"]

        self.definitions = [Definition(x) for x in json["definitions"]]


class Word:
    def __init__(self, word: str) -> None:
        self.word: str = word
        self.phonetic_audio: str = None
        self.phonetic_text: str = None
        self.meanings: list[Meaning] = None
        self.errored: bool = False
        self.invalid: bool = False

    def parse_meaning(self, api_res: dict):
        self.word = api_res["word"]

        # extract phonetic text and audio
        # assuming the API always returns atleast one phonetic entry
        desired_audio_url_pattern = re.compile(r"((us)|(uk)).mp3$")
        for entry in api_res["phonetics"]:
            if "audio" in entry:
                if entry["audio"] and desired_audio_url_pattern.search(entry["audio"]):
                    self.phonetic_audio = entry["audio"]
                    self.phonetic_text = entry["text"]
                    break
            else:
                self.phonetic_text = entry["text"]

        # diff meaning for each part of speech
        self.meanings = [Meaning(x) for x in api_res["meanings"]]

    def get_meaning(self):
        try:
            r = requests.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{self.word}"
            )
            if r.status_code == 404:
                self.invalid = True
                return
            r.raise_for_status()
            self.parse_meaning(r.json()[0])
        except requests.RequestException:
            self.errored = True


def build_context_dict(w: Word) -> dict:
    d = vars(w)
    d["meanings"] = [vars(x) for x in w.meanings]
    for x in d["meanings"]:
        x["definitions"] = [vars(y) for y in x["definitions"]]
    return d


async def start_cmd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = reply_book[MSGS.START_MSG]
    await update.effective_chat.send_message(text=reply)


async def help_cmd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = reply_book[MSGS.HELP_MSG]
    await update.effective_chat.send_message(reply)


async def define_txt_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_match = DEFINE_REGEX.fullmatch(update.message.text)
    if not msg_match:
        await update.effective_message.reply_text(reply_book[MSGS.SYNTAX_ERROR_MSG])
        return

    word = Word(msg_match.group("word"))
    word.get_meaning()
    if word.errored:
        await update.effective_chat.send_message(reply_book[MSGS.GENERAL_ERROR_MSG])
        return
    if word.invalid:
        # candidates returns none if no guesses
        probable_words = spell_checker.candidates(word.word)
        reply = invalid_word_template.render({"suggestions": probable_words})
        await update.effective_message.reply_text(reply)
        return
    replies = reply_define_template.render({"d": build_context_dict(word)}).split(
        "----"
    )
    for r in replies:
        r = r.strip()
        if r:
            await update.effective_chat.send_message(r)


# register buttons for the menu
async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([("help", "get help msg on using the bot")])


# build the bot app
defaults = Defaults(parse_mode=ParseMode.HTML)
application = (
    ApplicationBuilder().token(TOKEN).defaults(defaults).post_init(post_init).build()
)

# define handlers
start_cmd_handler = CommandHandler("start", start_cmd_cb)
help_cmd_handler = CommandHandler("help", help_cmd_cb)
define_cmd_handler = MessageHandler(filters.TEXT, define_txt_cb)

# register the handlers
application.add_handler(start_cmd_handler)
application.add_handler(help_cmd_handler)
application.add_handler(define_cmd_handler)

# run the bot
application.run_polling(poll_interval=3)
