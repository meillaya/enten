from __future__ import annotations

import asyncio
import shlex
import signal
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg
from opentelemetry import trace

from enten.config import LLMProvider, ReviewGenerator, Settings
from enten.github.client import GitHubClient
from enten.migration_operations import asyncpg_url
from enten.pipeline import FindingsGenerator
from enten.queue import init_queue
from enten.review.contract import review_contract_digest
from enten.review.remote import GitPublicCheckoutValidator, SandboxVerifier


class WorkerConfigurationError(RuntimeError):
    pass


class LogSink(Protocol):
    def info(self, event: str, **values: object) -> Any: ...


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    pool: asyncpg.Pool
    github_client: GitHubClient
    findings_generator: FindingsGenerator
    remote_verifier: SandboxVerifier
    checkout_validator: GitPublicCheckoutValidator
    verification_command: tuple[str, ...]
    review_contract: str


def configure_worker_observability() -> None:
    try:
        from enten.otel import configure_otel
    except Exception:
        return
    configure_otel()


def worker_tracer() -> trace.Tracer:
    return trace.get_tracer("enten.worker")


def build_production_github_client(settings: Settings) -> GitHubClient:
    if settings.github_app_id is None:
        raise WorkerConfigurationError("GITHUB_APP_ID is required by the production worker")
    if settings.github_app_private_key_path is None:
        raise WorkerConfigurationError(
            "GITHUB_APP_PRIVATE_KEY_PATH is required by the production worker"
        )
    if not settings.github_app_private_key_path.is_file():
        raise WorkerConfigurationError(
            "GITHUB_APP_PRIVATE_KEY_PATH must name a readable private-key file"
        )
    return GitHubClient(settings)


def build_findings_generator(settings: Settings) -> FindingsGenerator:
    """Return the configured findings generator, failing before the queue opens."""
    if settings.review_generator == ReviewGenerator.LOCAL:
        from enten.review.local import LocalFindingsGenerator

        return LocalFindingsGenerator()

    if settings.llm_provider == LLMProvider.OPENAI_COMPAT and not settings.llm_openai_api_key:
        raise WorkerConfigurationError(
            "LLM_OPENAI_API_KEY is required when REVIEW_GENERATOR=llm"
        )
    if settings.llm_provider == LLMProvider.ANTHROPIC and not settings.anthropic_api_key:
        raise WorkerConfigurationError(
            "ANTHROPIC_API_KEY is required when REVIEW_GENERATOR=llm"
        )

    from enten.llm import LLMClient, create_llm_provider
    from enten.review.llm import LLMFindingsGenerator

    provider = create_llm_provider(settings)
    return LLMFindingsGenerator(LLMClient(provider, settings))


def parse_verification_command(settings: Settings) -> tuple[str, ...]:
    command = tuple(shlex.split(settings.verification_command))
    if not command:
        raise WorkerConfigurationError("VERIFICATION_COMMAND cannot be empty")
    return command


async def bootstrap_worker(settings: Settings) -> WorkerRuntime:
    github_client = build_production_github_client(settings)
    verification_command = parse_verification_command(settings)
    if settings.sandbox_docker_image is None:
        await github_client.close()
        raise WorkerConfigurationError("SANDBOX_DOCKER_IMAGE is required by the production worker")
    review_contract = review_contract_digest(
        settings.verification_command,
        settings.sandbox_docker_image,
    )
    try:
        from enten.sandbox.factory import get_provider

        findings_generator = build_findings_generator(settings)
        remote_verifier = SandboxVerifier(get_provider(settings))
        pool = await asyncpg.create_pool(
            asyncpg_url(settings.database_url),
            min_size=1,
            max_size=4,
        )
        init_queue(pool)
    except BaseException:
        await github_client.close()
        raise
    return WorkerRuntime(
        pool=pool,
        github_client=github_client,
        findings_generator=findings_generator,
        remote_verifier=remote_verifier,
        checkout_validator=GitPublicCheckoutValidator(),
        verification_command=verification_command,
        review_contract=review_contract,
    )


def install_shutdown_handlers(
    event: asyncio.Event,
    logger: LogSink,
    worker_id: str,
) -> None:
    loop = asyncio.get_running_loop()

    def on_signal(sig_name: str) -> None:
        logger.info("shutdown_signal_received", signal=sig_name, worker_id=worker_id)
        event.set()

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, on_signal, sig.name)
    except (ValueError, NotImplementedError):
        return


__all__ = [
    "WorkerConfigurationError",
    "WorkerRuntime",
    "bootstrap_worker",
    "build_findings_generator",
    "build_production_github_client",
    "configure_worker_observability",
    "install_shutdown_handlers",
    "parse_verification_command",
    "worker_tracer",
]
