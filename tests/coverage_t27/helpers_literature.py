"""Re-exports of the literature test doubles so the coverage tests share one set of fakes."""

from tests.literature.lit_fakes import Router, openalex_work  # noqa: F401
from tests.literature.test_providers import CR, OA, S2, make_client  # noqa: F401
