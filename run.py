import traceback
import sys
from discord.ext import commands
from tokenfile import token

startup_cogs = ["cogs.serversaver"]

bot = commands.Bot(command_prefix="?")


@bot.event
async def on_ready():
    print("Logged in as {}".format(bot.user))
    print("-"*5)


if __name__ == '__main__':
    for extension in startup_cogs:
        try:
            bot.load_extension(extension)
        except Exception as e:
            print(f'Failed to load extension {extension}.', file=sys.stderr)
            traceback.print_exc()


bot.run(token)