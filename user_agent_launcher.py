"""Launch the User Agent in local threads and wait."""

from __future__ import annotations

import argparse
import asyncio
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from academy.exchange.cloud.client import HttpExchangeFactory
from academy.exchange.cloud.login import get_token_storage
from academy.exchange.cloud.scopes import AcademyExchangeScopes
from academy.logging import init_logging
from academy.manager import Manager

from app.modules.term_extractor.user_agent import UserAgent


DEFAULT_TOKEN_DB = Path('~/local/share/academy/storage.db').expanduser()
TOKEN_EXPIRY_SKEW_S = 60


def get_cached_auth_headers(token_db: Path = DEFAULT_TOKEN_DB) -> dict[str, str]:
    """Load the Academy Exchange access token from the shared Academy cache."""
    storage = get_token_storage(filepath=token_db)
    token_data = storage.get_token_data(AcademyExchangeScopes.resource_server)

    if token_data is None:
        raise RuntimeError(
            f'No Academy Exchange token found in {token_db}. '
            'Run the main agent auth flow first, then retry this launcher.',
        )

    if token_data.expires_at_seconds <= time.time() + TOKEN_EXPIRY_SKEW_S:
        raise RuntimeError(
            f'The cached Academy Exchange access token in {token_db} is expired '
            'or about to expire. Re-run the main agent auth flow to refresh it.',
        )

    token_type = token_data.token_type or 'Bearer'
    return {'Authorization': f'{token_type} {token_data.access_token}'}


async def launch(port: int, token_db: Path = DEFAULT_TOKEN_DB) -> None:
    """Launch UserAgent in ThreadPoolExecutor and write handle info to file."""
    init_logging()
    auth_headers = get_cached_auth_headers(token_db)

    async with await Manager.from_exchange_factory(
        factory=HttpExchangeFactory(auth_method=None, additional_headers=auth_headers),
        executors=ThreadPoolExecutor(max_workers=4),
    ) as manager:
        agent_hdl = await manager.launch(UserAgent, kwargs={'port': port})
        print(f'User Agent Handle >>>> {agent_hdl.agent_id.uid!s}')

        with open('user_agent_handle.pkl', 'wb') as f:
            pickle.dump(agent_hdl.agent_id, f)
        await manager.wait([agent_hdl], raise_error=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port', default=8000, type=int, help='Port at which the flask service is listening')
    parser.add_argument('-u', '--user_agent_id', help='User Agent will use this ID if specified')
    parser.add_argument(
        '--token-db',
        default=DEFAULT_TOKEN_DB,
        type=Path,
        help='Academy Globus token cache to reuse',
    )
    args = parser.parse_args()
    asyncio.run(launch(port=args.port, token_db=args.token_db.expanduser()))