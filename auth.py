from academy.exchange.cloud.client import HttpExchangeFactory
from academy.manager import Manager

factory = HttpExchangeFactory(
    "https://exchange.academy-agents.org",
    auth_method="globus",
)
# This triggers the Globus login and caches the token
import asyncio
asyncio.run(factory.create_user_client())
