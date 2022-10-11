import asyncio
import discord
import pydactyl
import sqlite3
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from dotenv import load_dotenv
from os import getenv, listdir, path
from time import time as time_seconds

# # Setup .env
if not path.exists('.env'):
    with open('.env', 'w') as environment:
        environment.write('bot_token=yourToken\npterodactyl_site=https://example.com\napi_key=yourApiKey')
    quit(f'please configure the .env file')


# # bot class setup
class DisMine(commands.AutoShardedBot):
    async def setup_hook(self):
        print(f'{current_time()} - [INFO] loading cogs')
        if path.exists(f'{bot_location}cogs'):
            for file in listdir(f'{bot_location}cogs'):
                if file.endswith('.py'):
                    try:
                        await bot.load_extension(f'cogs.{file[:-3]}')
                    except Exception as e:
                        print(f'{current_time()} - [ERROR] Loading cog: {file[:-3]} reason: {e}')


# # variables setup
# logic variables
startup = True
running_servers = []
variables_synced = False
# bot variables
load_dotenv()
bot_location = f'{path.dirname(path.abspath(__file__))}/'
intents = discord.Intents.default()
intents.message_content = True
bot = DisMine(command_prefix='lc!', intents=intents, help_command=None)
# api variables
app = pydactyl.Application(url=getenv('pterodactyl_site'), api_key=getenv('api_key'))
panel_users = {}  # asyncio.run(app.get_users())['data']
panel_servers = {}  # asyncio.run(app.get_servers())['data']
panel_allocations = {}  # asyncio.run(app.get_node_allocations(1))['data']

# # db setup
connection = sqlite3.connect('data.db')
cursor = connection.cursor()
cursor.execute(
    """CREATE TABLE IF NOT EXISTS users (
                                    id INTEGER NOT NULL PRIMARY KEY,
                                    credits INTEGER,
                                    premium BOOLEAN,
                                    server_status BOOLEAN,
                                    last_online INTEGER,
                                    stop_server BOOLEAN
                                );"""
)
connection.commit()


# # functions
def current_time():
    return datetime.now().strftime('%d/%m/%Y %H:%M:%S')


def db_get(command: str, values: tuple):
    try:
        output = cursor.execute(command, values).fetchone()
        return output
    except Exception as e:
        print(f'{current_time()} - {e}')
        return False


def db_exec(command: str, values: tuple):
    try:
        cursor.execute(command, values)
        connection.commit()
        return True
    except Exception as e:
        print(f'{current_time()} - {e}')
        return False


async def clear_queue():
    server_count = 0
    for server in panel_servers:
        if not server['attributes']['suspended']:
            if server['attributes']['id'] not in (1, 3):
                output = await app.suspend_server(server['attributes']['id'])
                if output.status == 204:
                    server_count += 1
                else:
                    print(
                        f'{current_time()} - [ERROR] suspending server: {server["attributes"]["id"]} | '
                        f'code: {output.status}'
                    )
    print(f'{current_time()} - [INFO] Cleared queue stopped {server_count} servers from running')


async def credit_reduction(person, server, ctx):
    # give person some time to start the server
    await asyncio.sleep(60)

    # while user has coins keep server alive
    while person.get_credits() > 0:
        person.update_credits(-1)

        if person.get_credits() == 0 and not person.stop_server():
            await ctx.author.send('You are running out of credits. Your server will stop in 60 seconds.')

        if person.stop_server():
            await ctx.author.send('Your server will stop in 60 seconds.')

        await asyncio.sleep(60)

        if person.stop_server():
            person.set_stop_server(False)
            break

    await ctx.author.send('Your server has been stopped. Thanks for using and supporting Nextpie ❤')
    print(f'{current_time()} - [INFO] Stopping server {server["attributes"]["id"]}')
    return await app.suspend_server(server['attributes']['id'])


async def is_synced(ctx):
    global variables_synced
    return variables_synced


# # classes
class Person:
    def __init__(self, user_id):
        self.user_id = user_id
        self.exists = True if db_get('SELECT * FROM users WHERE id=?;', (self.user_id,)) else False
        try:
            self.premium = True if db_get('SELECT premium FROM users WHERE id=?;', (self.user_id,))[0] else False
        except TypeError:
            self.premium = False

    def init(self, amount: int, premium: bool):
        output = db_exec(
            'INSERT INTO users (id, credits, premium, server_status, last_online, stop_server) '
            'VALUES (?, ?, ?, ?, ?, ?);',
            (self.user_id, amount, premium, False, time_seconds(), False)
        )
        return output

    def get_credits(self):
        output = db_get('SELECT credits FROM users WHERE id=?', (self.user_id,))
        if output and isinstance(output, tuple):
            credits = output[0]
        else:
            credits = 0
        return credits

    def update_credits(self, amount: int):
        if self.exists:
            new_amount = db_get('SELECT credits FROM users WHERE id=?', (self.user_id,))[0] + amount
            output = db_exec('UPDATE users SET credits=? WHERE id=?', (new_amount, self.user_id))
        else:
            output = self.init(amount, False)
        return output

    def set_server_status(self, status: bool):
        if self.exists:
            output = db_exec('UPDATE users SET server_status=? WHERE id=?', (status, self.user_id))
            return output
        else:
            return False

    def has_server(self):
        if self.exists:
            output = db_get('SELECT server_status FROM users WHERE id=?', (self.user_id,))
            return output[0]
        else:
            return False

    def set_stop_server(self, status=bool):
        if self.exists:
            return db_exec('UPDATE users SET stop_server=? WHERE id=?', (status, self.user_id))
        else:
            return False

    def stop_server(self):
        if self.exists:
            output = db_get('SELECT stop_server FROM users WHERE id=?', (self.user_id,))
            return output[0]
        else:
            return False

    def set_premium(self, status: bool):
        if self.exists:
            output = db_exec('UPDATE users SET premium=? WHERE id=?', (status, self.user_id))
        else:
            output = self.init(0, status)
        self.premium = status
        return output


class RegisterButtons(discord.ui.View):
    def __init__(self, ctx, email, first_name, last_name, timeout=60):
        self.ctx = ctx
        self.email = email
        self.first_name = first_name
        self.last_name = last_name
        super().__init__(timeout=timeout)

    @discord.ui.button(label='Agree', style=discord.ButtonStyle.green)
    async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        response = await app.create_user(
            email=self.email,
            first_name=self.first_name,
            last_name=self.last_name,
            username=self.ctx.author.id
        )

        if 'errors' in response:
            if response['errors'][0]['detail'] == 'The username has already been taken.':
                return await interaction.response.edit_message(content='You already have an account.', view=None)
            return await interaction.response.edit_message(content=response['errors'][0]['detail'], view=None)

        print(
            f'{current_time()} - [INFO] Created an account for '
            f'{self.ctx.author.display_name}#{self.ctx.author.discriminator}'
        )
        return await interaction.response.edit_message(
            content='Created your account! Please check your email to verify your account. '
                    '(**It can take up to 5 minutes for everything to sync**)',
            view=None
        )

    @discord.ui.button(label='Disagree', style=discord.ButtonStyle.red)
    async def disagree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(
            content='You need to accept the terms to use DisMine. https://nextpie.nl/terms-of-service',
            view=self
        )


# # events
@bot.event
async def on_ready():
    global startup, variables_synced
    print(f'{current_time()} - [INFO] {bot.user.name} syncing command tree')
    # await bot.tree.sync()
    if startup:
        update_cache.start()
        print(f'{current_time()} - [INFO] Sleeping 5 seconds to allow cache to sync')
        await asyncio.sleep(5)
        await clear_queue()
        purge_servers.start()
        startup = False
        variables_synced = True
    print(f'{current_time()} - [INFO] {bot.user.name} connected to a shard')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='your server'))


@tasks.loop(seconds=300)
async def update_cache():
    start_time = time_seconds()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='refreshing cache'))
    global panel_users, panel_servers, panel_allocations
    panel_users = (await app.get_users())['data']
    panel_servers = (await app.get_servers())['data']
    panel_allocations = (await app.get_node_allocations(1))['data']
    break_time = str((time_seconds() - start_time)).split('.')
    total_time = break_time[0] + '.' + break_time[1][:4] + '...'
    print(f'{current_time()} - [INFO] Updated local cache took {total_time} seconds')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='your server'))


@tasks.loop(hours=24)
async def purge_servers():
    start_time = time_seconds()
    print(f'{current_time()} - [INFO] Purging servers')
    server_count = 0
    print(
        f'{current_time()} - [INFO] Purge done removed {server_count} servers '
        f'took {time_seconds() - start_time} seconds'
    )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        time = str(timedelta(seconds=error.retry_after)).split(':')
        message = await ctx.send(
            f'Please wait `{time[0]}`:`{time[1]}`:`{(time[2])[:2]}` before using this command.'
        )

        if error.retry_after < 5:
            loop_count = int(error.retry_after)
        else:
            loop_count = 5

        for i in range(1, loop_count):
            time = str(timedelta(seconds=error.retry_after - i)).split(':')
            await message.edit(
                content=f'Please wait `{time[0]}`:`{time[1]}`:`{(time[2])[:2]}` before using this command.'
            )
            await asyncio.sleep(1)
        return await message.delete()


# # commands
@bot.hybrid_command(description='Help command if you get stuck.')
async def help(ctx):
    return await ctx.send('Coming soon.')


@bot.hybrid_command(description='Sends an invite link for the support server.')
async def support(ctx):
    return await ctx.send('https://discord.gg/QdQYEpzeXp')


@bot.hybrid_command(description='Sends an url for the terms of service.')
async def terms(ctx):
    return await ctx.send('https://nextpie.nl/terms-of-service/')


@bot.hybrid_command(description='Sends an url for the server panel.')
async def panel(ctx):
    return await ctx.send('https://panel.nextpie.nl')


@bot.hybrid_command(description='Creates an account on panel.nextpie.nl to manage your server. (USE IN DM)')
@commands.cooldown(2, 60, commands.BucketType.user)
async def register(ctx, email: str):  # , first_name: str, last_name: str):
    # warn if not DM
    if ctx.message.guild:
        await ctx.send(f'{ctx.author.mention} **You are not in DM this will allow everyone to see your email.**')
        await asyncio.sleep(5)

    # check Discord user
    account_age = (datetime.now(timezone.utc) - ctx.author.created_at).days
    required_age = 90
    if account_age < required_age:
        return await ctx.send(f'Your Discord account must be older than {required_age} days to use DisMine.')

    # required for privacy policy
    first_name = 'John'
    last_name = 'Doe'

    # check if email is allowed (anti 10 minute mail)
    email_allowed = False
    for provider in (
        'outlook',
        'gmail',
        'live',
        'hotmail',
        'icloud',
        'me.com',
        'mac.com',
        'aol.com',
        'yahoo',
        'protonmail',
        'pm.com',
        'zoho.com',
        'yandex',
        'titan.email',
        'gmx.com',
        'hubspot.com',
        'mail.com'
    ):
        try:
            if provider in email.split('@')[1]:
                email_allowed = True
                break
        except IndexError:
            continue

    if not email_allowed:
        return await ctx.send(
            'This email looks malformed, make sure to use a legitimate email provider like Gmail, Outlook or iCloud. '
            'If you think this is a mistake create a ticket in the support server (`/support`).'
        )

    await ctx.send(
        'I hereby agree to have read the terms of service and will follow these as provided. '
        'https://nextpie.nl/terms-of-service',
        view=RegisterButtons(ctx, email, first_name, last_name)
    )


# add confirmation before release
@bot.hybrid_command(description='Removes all your data including account and servers.')
@commands.check(is_synced)
@commands.cooldown(1, 3600, commands.BucketType.user)
async def withdraw(ctx):
    message = await ctx.send('Collecting your data... please wait')
    for user in panel_users:
        if user['attributes']['username'] == str(ctx.author.id):
            user_panel_id = user['attributes']['id']

            # remove user servers
            for server in panel_servers:
                if server['attributes']['user'] == user_panel_id:
                    output = await app.delete_server(server['attributes']['id'])

            # remove user
            output = await app.delete_user(user_panel_id)

            # remove user from local database
            db_exec('DELETE FROM users WHERE id=?', (ctx.author.id,))
            
            if 'errors' in output:
                print(f'{current_time()} - [ERROR] User {user_panel_id} | {output["errors"][0]["detail"]}')
            else:
                print(f'{current_time()} - [INFO] Succesfully removed user({user_panel_id}) and servers')

            return await message.edit(
                content='Sorry to see you go... It may take some time for all your data to be removed.'
            )

    return await message.edit(content='Cannot find any data connected to this account.')


@bot.hybrid_command(description='Shows your current credits.')
async def credits(ctx):
    return await ctx.send(f'You have `{Person(user_id=ctx.author.id).get_credits()}` credit(s).')


@bot.hybrid_command(description='Daily 60 credits which translate into 1 hour a day.')
@commands.cooldown(1, 79200, commands.BucketType.user)
async def daily(ctx):
    person = Person(user_id=ctx.author.id)
    if person.premium:
        amount = 120
    else:
        amount = 60
    person.update_credits(amount)
    return await ctx.send(f'You got `{amount}` credit(s).')


@bot.hybrid_command(description='Vote on top.gg to get more credits.')
async def vote(ctx):
    return await ctx.send('Coming soon.')


# 400% cpu | 3GB(3072) ram | 1GB(1024) storage | per server
@bot.hybrid_command(description='Start or create your server.')
@commands.check(is_synced)
@commands.cooldown(2, 60, commands.BucketType.user)
async def start(ctx):
    global panel_users, panel_allocations, running_servers

    # create user if not exists
    person = Person(user_id=ctx.author.id)

    if person.get_credits() < 1:
        return await ctx.send('You don\'t have enough credits.')

    if len(running_servers) == 4:
        return await ctx.send('The maximum active servers have been reached. Check the queue time with `/queue`.')

    for user in panel_users:
        if user['attributes']['username'] == str(ctx.author.id):
            user_panel_id = user['attributes']['id']

            # check if user has server
            for server in panel_servers:
                if server['attributes']['user'] == user_panel_id:
                    # check if server is already unsuspended
                    if (await app.get_server(server['attributes']['id']))['attributes']['suspended']:
                        output = await app.unsuspend_server(server['attributes']['id'])
                        if output.status == 204:
                            print(f'{current_time()} - [INFO] Starting server {server["attributes"]["id"]}')
                            running_servers.append(ctx.author.id)
                            await ctx.send(
                                'Setting up your server visit https://panel.nextpie.nl to start it. '
                                '(You get an extra minute to start your server)'
                            )

                            # start credit deduction loop
                            output = await credit_reduction(person, server, ctx)
                            if output.status != 204:
                                return print(
                                    f'{current_time()} - [ERROR] Stopping server {server["attributes"]["id"]} | '
                                    f'code: {output.status}'
                                )
                            running_servers.remove(ctx.author.id)
                            return

                        elif output.status == 500:
                            return await ctx.send('Something went wrong starting your server, please try again.')
                        else:
                            print(
                                f'{current_time()} - [ERROR] starting server {server["attributes"]["id"]} | '
                                f'code: {output.status}'
                            )
                            return await ctx.send(
                                'Something unusual went wrong starting your server, please try again.'
                            )
                    else:
                        return await ctx.send(
                            'Server already active, you may need to manually start it on https://panel.nextpie.nl'
                        )

            if not person.has_server():
                # user has no server create one
                for allocation in panel_allocations:
                    if not allocation['attributes']['assigned']:
                        # Paper MC server
                        server = await app.create_server(
                            name="DisMine - MC paper",
                            user_id=user_panel_id,
                            nest_id=1,
                            egg_id=2,
                            docker_image="ghcr.io/pterodactyl/yolks:java_17",
                            startup="java -Xms128M -XX:MaxRAMPercentage=95.0 -Dterminal.jline=false -Dterminal.ansi=true -jar {{SERVER_JARFILE}}",
                            environment={
                                "SERVER_JARFILE": "server.jar",
                                "MINECRAFT_VERSION": "latest",
                                "BUILD_NUMBER": "latest",
                            },
                            default_allocation=allocation['attributes']['id']
                        )
                        await ctx.send(
                            'Creating your server visit https://panel.nextpie.nl to configure it. '
                            'You will be prompted to accept the Minecraft EULA.'
                        )

                        print(f'{current_time()} - [INFO] Starting server {server["attributes"]["id"]}')

                        # set required variables
                        running_servers.append(ctx.author.id)
                        person.set_server_status(True)

                        # start credit deduction loop
                        output = await credit_reduction(person, server, ctx)
                        if output.status != 204:
                            return print(
                                f'{current_time()} - [ERROR] Stopping server {server["attributes"]["id"]} | '
                                f'code: {output.status}'
                            )
                        running_servers.remove(ctx.author.id)
                        return

                print(f'{current_time()} - [ERROR] No more allocations')
                return await ctx.send(f'Something went wrong... Go to the support server for help.')
            else:
                return await ctx.send(
                    'Server already active, you may need to manually start it on https://panel.nextpie.nl'
                )

    return await ctx.send('Cannot find your account, if you just registered it can take up to 5 minutes to sync.')


@bot.hybrid_command(description='Stop your running server.')
@commands.check(is_synced)
@commands.cooldown(2, 60, commands.BucketType.user)
async def stop(ctx):
    if ctx.author.id in running_servers:
        person = Person(user_id=ctx.author.id)
        person.set_stop_server(True)
        return await ctx.send('Stopping your server. When your current credit runs out your server will stop.')
    return await ctx.send('You do not have a server running.')


@bot.hybrid_command(description='Shows the average time till your server shutsdown.')
@commands.check(is_synced)
@commands.cooldown(2, 60, commands.BucketType.user)
async def remaining(ctx):
    global running_servers
    if ctx.author.id in running_servers:
        person = Person(user_id=ctx.author.id)
        if person.stop_server():
            time_remaining = 1
        else:
            time_remaining = (person.get_credits() + 1)
        return await ctx.send(f'You have `{time_remaining}` minutes left, before your server stops.')
    return await ctx.send('You do not have a server running.')


@bot.hybrid_command(description='See the current wait time.')
@commands.check(is_synced)
@commands.cooldown(2, 20, commands.BucketType.user)
async def queue(ctx):
    global running_servers

    if running_servers and len(running_servers) == 4:
        minimal_credits = 999999999
        for user in running_servers:
            person = Person(user)
            if person.stop_server():
                user_credits = 0
            else:
                user_credits = person.get_credits()

            if user_credits < minimal_credits:
                minimal_credits = user_credits + 1

        return await ctx.send(f'The current wait time is `{minimal_credits * 5}` minutes.')
    else:
        return await ctx.send(f'There is no queue at the moment.')


# Pterodactyl requirement
print('started')

# run bot
bot.run(token=getenv('bot_token'), log_level=0)
