"""`choregos_core.telemetry` without OpenTelemetry: the `telemetry` extra is absent (S62-07).

OpenTelemetry is an extra of `choregos-core`, asked for by the API, the orchestrator and the runner;
the CLI does not ask for it, and its native binary carries none. The module must still import there,
leave tracing off, and turn every helper into a no-op — in a fresh interpreter where any import of
`opentelemetry` fails, as it does when the package is not installed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import importlib.abc, os, sys

    class NoOpenTelemetry(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name == "opentelemetry" or name.startswith("opentelemetry."):
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    sys.meta_path.insert(0, NoOpenTelemetry())
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://collector.invalid:4318"

    from choregos_core import telemetry

    assert telemetry.AVAILABLE is False
    assert telemetry.configure_tracing("choregos-test") is False
    with telemetry.stage_span("run-1", stage="build", project="p", workflow="w") as span:
        telemetry.record_correlation(span, project="p", work_item="P-1", stage="build")
        telemetry.record_outcome(span, "failed", failed=True)
        assert span.is_recording() is False
    with telemetry.agent_span("run-1", trace_parent=telemetry.traceparent("run-1"), agent="dev"):
        pass
    with telemetry.tool_span("run-1", "search"):
        telemetry.record_tool_call(status_code=500, provider="github", project="p")
        telemetry.record_tool_origin("script", "0" * 64)
    telemetry.shutdown_tracing()
    assert telemetry.traceparent("run-1").startswith("00-")
    assert not any(m == "opentelemetry" or m.startswith("opentelemetry.") for m in sys.modules)
    print("no-op")
    """
)


def test_without_the_extra_tracing_stays_off_and_every_helper_is_a_no_op() -> None:
    result = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no-op"


def test_with_the_extra_tracing_is_available() -> None:
    from choregos_core import telemetry

    assert telemetry.AVAILABLE is True
