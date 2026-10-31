"""Phase 0 probe spike: measure confirmed Python findings on small cases.

This is intentionally a measurement harness, not a product path. It runs the
real hypothesis generator and local probe runner against a handful of small
buggy modules. Replace the case list with the full 20-PR corpus before using
the result as the go/no-go gate.
"""

from __future__ import annotations

import asyncio
import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from enten.config import Settings


@dataclass(frozen=True)
class Case:
    name: str
    before: str
    after: str
    bug_statement: str


CASES = (
    Case(
        "wrong_operator",
        "def add(a, b):\n    return a + b\n",
        "def add(a, b):\n    return a - b\n",
        "Addition was changed to subtraction.",
    ),
    Case(
        "off_by_one",
        "def top_n(items, n):\n    return items[:n]\n",
        "def top_n(items, n):\n    return items[:n - 1]\n",
        "The slice drops one requested item.",
    ),
    Case(
        "boundary",
        "def is_adult(age):\n    return age >= 18\n",
        "def is_adult(age):\n    return age > 18\n",
        "Age 18 is no longer an adult.",
    ),
    Case(
        "integer_division",
        "def average(values):\n    return sum(values) / len(values)\n",
        "def average(values):\n    return sum(values) // len(values)\n",
        "The average truncates instead of returning a float.",
    ),
    Case(
        "key_error",
        "def find_user(users, name):\n    return users.get(name)\n",
        "def find_user(users, name):\n    return users[name]\n",
        "A missing user now raises KeyError.",
    ),
    Case(
        "unsafe_eval",
        "def parse(text):\n    return int(text)\n",
        "def parse(text):\n    return eval(text)\n",
        "Arbitrary expressions are evaluated.",
    ),
    Case(
        "wrong_last",
        "def last(items):\n    return items[-1]\n",
        "def last(items):\n    return items[-2]\n",
        "The last item is skipped.",
    ),
    Case(
        "range_off_by_one",
        "def count(n):\n    return len(range(n))\n",
        "def count(n):\n    return len(range(n - 1))\n",
        "The count is one short.",
    ),
    Case(
        "rounding",
        "def total(values):\n    return round(sum(values), 2)\n",
        "def total(values):\n    return round(sum(values), 1)\n",
        "The total loses precision.",
    ),
    Case(
        "reverse_sort",
        "def ranked(values):\n    return sorted(values)\n",
        "def ranked(values):\n    return sorted(values, reverse=True)\n",
        "The ranking order is reversed.",
    ),
    Case(
        "mutating_sort",
        "def ordered(values):\n    return sorted(values)\n",
        "def ordered(values):\n    values.sort()\n    return values\n",
        "The input is mutated.",
    ),
    Case(
        "identity_compare",
        "def same(a, b):\n    return a == b\n",
        "def same(a, b):\n    return a is b\n",
        "Identity is used instead of equality.",
    ),
    Case(
        "swallowed_exception",
        "def parse(text):\n    return int(text)\n",
        (
            "def parse(text):\n"
            "    try:\n"
            "        return int(text)\n"
            "    except Exception:\n"
            "        return 0\n"
        ),
        "Invalid input is silently converted to zero.",
    ),
    Case(
        "empty_average",
        "def average(values):\n    return sum(values) / len(values)\n",
        (
            "def average(values):\n"
            "    try:\n"
            "        return sum(values) / len(values)\n"
            "    except Exception:\n"
            "        return 0\n"
        ),
        "An empty input silently becomes zero.",
    ),
    Case(
        "index_vs_find",
        "def find(text, needle):\n    return text.find(needle)\n",
        "def find(text, needle):\n    return text.index(needle)\n",
        "A missing needle raises instead of returning -1.",
    ),
    Case(
        "default_mutation",
        "def append(value, items):\n    items.append(value)\n    return items\n",
        "def append(value, items=[]):\n    items.append(value)\n    return items\n",
        "The default list is shared across calls.",
    ),
    Case(
        "case_sensitive_key",
        "def key(name):\n    return name.lower()\n",
        "def key(name):\n    return name\n",
        "Keys are no longer normalized.",
    ),
    Case(
        "wrong_floor",
        "def clamp(value):\n    return max(0, min(value, 10))\n",
        "def clamp(value):\n    return max(0, min(value, 9))\n",
        "The upper bound is wrong.",
    ),
    Case(
        "dict_pop",
        "def remove(data, name):\n    return data.pop(name, None)\n",
        "def remove(data, name):\n    return data.pop(name)\n",
        "A missing key raises.",
    ),
    Case(
        "split_limit",
        "def parts(text):\n    return text.split(',')\n",
        "def parts(text):\n    return text.split(',', 1)\n",
        "Only one split is performed.",
    ),
)


def _diff(case: Case) -> str:
    return "".join(
        difflib.unified_diff(
            case.before.splitlines(keepends=True),
            case.after.splitlines(keepends=True),
            fromfile="a/app.py",
            tofile="b/app.py",
        )
    )


def _load_environment() -> None:
    key_file = Path.home() / "enten-live.env"
    values: dict[str, str] = {}
    if key_file.exists():
        for line in key_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    api_key = values.get("OPENAI_API_KEY", "")
    if not api_key.startswith("sk-or-"):
        raise SystemExit("OPENAI_API_KEY with an OpenRouter key is required")
    os.environ.update(
        {
            "LLM_PROVIDER": "openai-compat",
            "LLM_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            "LLM_OPENAI_API_KEY": api_key,
            "LLM_DEFAULT_MODEL": "deepseek/deepseek-v4.1-flash-1017",
            "DATABASE_URL": "postgresql+asyncpg://enten:enten@localhost:5433/enten",
            "PER_PR_COST_CAP_USD": "0.50",
        }
    )


async def _run_case(case: Case, settings: Settings) -> tuple[int, int, int]:
    from enten.llm import LLMClient, create_llm_provider
    from enten.review.diff import DiffIndex
    from enten.review.hypothesis import LLMHypothesisGenerator
    from enten.review.python_probes import LocalProbeRunner
    from enten.review.run import Proposal

    with TemporaryDirectory(prefix=f"enten-spike-{case.name}-") as raw:
        root = Path(raw)
        (root / "app.py").write_text(case.after, encoding="utf-8")
        diff = _diff(case)
        proposal = Proposal(
            repository_path=root,
            files=("app.py",),
            verification_command=("true",),
            diff=diff,
            changed=DiffIndex.from_patch(diff),
        )
        generator = LLMHypothesisGenerator(
            LLMClient(create_llm_provider(settings), settings)
        )
        hypotheses = await generator.generate(proposal)
        probes = await LocalProbeRunner().run(hypotheses, proposal)
        confirmed = sum(1 for result in probes.results if result.status == "confirmed")
        rejected = sum(1 for result in probes.results if result.status == "rejected")
        inconclusive = sum(1 for result in probes.results if result.status == "inconclusive")
        print(
            f"{case.name}: hypotheses={len(hypotheses.hypotheses)} "
            f"confirmed={confirmed} rejected={rejected} inconclusive={inconclusive} "
            f"cost=${hypotheses.cost_usd}"
        )
        return len(hypotheses.hypotheses), confirmed, inconclusive


async def main() -> None:
    _load_environment()
    settings = Settings()
    total_hypotheses = 0
    total_confirmed = 0
    total_inconclusive = 0
    for case in CASES[:20]:
        try:
            hypotheses, confirmed, inconclusive = await _run_case(case, settings)
        except Exception as exc:
            print(f"{case.name}: error={type(exc).__name__}: {exc}")
            continue
        total_hypotheses += hypotheses
        total_confirmed += confirmed
        total_inconclusive += inconclusive
    print(
        f"TOTAL hypotheses={total_hypotheses} confirmed={total_confirmed} "
        f"inconclusive={total_inconclusive}"
    )


if __name__ == "__main__":
    asyncio.run(main())
