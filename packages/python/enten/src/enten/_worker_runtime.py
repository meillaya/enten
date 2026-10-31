from __future__ import annotations

import asyncio
import shlex
import signal
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg
from opentelemetry import trace

from enten.config import LLMProvider, ProbeMode, ReviewGenerator, Settings
from enten.github.client import GitHubClient
from enten.migration_operations import asyncpg_url
from enten.pipeline import FindingsGenerator, HypothesisGenerator
from enten.queue import init_queue
from enten.review.contract import review_contract_digest
from enten.review.python_probes import ProbeRunner, SandboxProbeRunner, parse_probe_command
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
    hypothesis_generator: HypothesisGenerator | None
    probe_runner: ProbeRunner | None
    probe_publish: bool
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


def build_hypothesis_generator(settings: Settings) -> HypothesisGenerator:
    """Build the Python hypothesis generator when probes are enabled."""
    if settings.review_generator != ReviewGenerator.LLM:
        raise WorkerConfigurationError("REVIEW_PROBES requires REVIEW_GENERATOR=llm")
    if settings.llm_provider == LLMProvider.OPENAI_COMPAT and not settings.llm_openai_api_key:
        raise WorkerConfigurationError(
            "LLM_OPENAI_API_KEY is required when REVIEW_PROBES is enabled"
        )
    if settings.llm_provider == LLMProvider.ANTHROPIC and not settings.anthropic_api_key:
        raise WorkerConfigurationError(
            "ANTHROPIC_API_KEY is required when REVIEW_PROBES is enabled"
        )

    from enten.llm import LLMClient, create_llm_provider
    from enten.review.hypothesis import LLMHypothesisGenerator

    return LLMHypothesisGenerator(
        LLMClient(create_llm_provider(settings), settings),
        settings.probe_max,
    )


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

        provider = get_provider(settings)
        findings_generator = build_findings_generator(settings)
        hypothesis_generator = None
        probe_runner = None
        if settings.review_probes != ProbeMode.OFF:
            hypothesis_generator = build_hypothesis_generator(settings)
            probe_runner = SandboxProbeRunner(
                provider,
                parse_probe_command(settings.probe_command),
                settings.probe_timeout_seconds,
            )
        remote_verifier = SandboxVerifier(provider)
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
        hypothesis_generator=hypothesis_generator,
        probe_runner=probe_runner,
        probe_publish=settings.review_probes == ProbeMode.ON,
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
    "build_hypothesis_generator",
    "build_production_github_client",
    "configure_worker_observability",
    "install_shutdown_handlers",
    "parse_verification_command",
    "worker_tracer",
]
