"""Run Academy's Globus login and cache the resulting access token."""

import asyncio

from academy.exchange.cloud.client import HttpExchangeFactory


async def authenticate() -> None:
    """Create an Academy exchange user client, which runs the Globus login and caches the token."""
    factory = HttpExchangeFactory(
        "https://exchange.academy-agents.org",
        auth_method="globus",
    )
    await factory.create_user_client()


def main() -> None:
    """Run ``authenticate`` to completion."""
    asyncio.run(authenticate())


if __name__ == "__main__":
    main()
