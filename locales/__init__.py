import json
import pathlib

from .locales import Locales

with open(pathlib.Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]

def setup(bot):
    bot.add_cog(Locales(bot))