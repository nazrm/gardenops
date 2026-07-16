"""Focused black-box tests for the standalone deterministic loopback fixture."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts" / "e2e" / "providers" / "deterministicLoopbackProvider.cjs"
READY_PREFIX = "GARDENOPS_PROVIDER_FIXTURE_READY="


class FixtureProcess:
    def __init__(self, scenario: str, *, timeout_ms: int = 200) -> None:
        self.process = subprocess.Popen(
            [
                "node",
                str(FIXTURE),
                "--scenario",
                scenario,
                "--port",
                "0",
                "--timeout-ms",
                str(timeout_ms),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        ready_line = self.process.stdout.readline().strip()
        assert ready_line.startswith(READY_PREFIX), self._error_output(ready_line)
        self.handoff = json.loads(ready_line.removeprefix(READY_PREFIX))

    def _error_output(self, ready_line: str) -> str:
        if self.process.stderr is None:
            return ready_line
        return f"{ready_line}\n{self.process.stderr.read()}"

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        self.process.wait(timeout=3)


@pytest.fixture
def fixture_process(request: pytest.FixtureRequest) -> FixtureProcess:
    process = FixtureProcess(str(request.param))
    try:
        yield process
    finally:
        process.close()


def _request(
    url: str,
    *,
    body: bytes | None = None,
    timeout: float = 2,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers or {},
        method=method or ("POST" if body else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _state(process: FixtureProcess) -> dict[str, object]:
    status, body = _request(process.handoff["state_url"])
    assert status == 200
    return json.loads(body)


def _openai_text(response_body: bytes) -> str:
    response = json.loads(response_body)
    return response["output"][0]["content"][0]["text"]


def _options_headers(url: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
        return response.status, {name.lower(): value for name, value in response.headers.items()}


def _assert_loopback_url(raw_url: str) -> None:
    parsed = urllib.parse.urlparse(raw_url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port and parsed.port > 0


@pytest.mark.parametrize("fixture_process", ["success", "partial"], indirect=True)
def test_success_and_partial_are_ai_compatible_and_counted(
    fixture_process: FixtureProcess,
) -> None:
    openai_url = f"{fixture_process.handoff['openai_base_url']}/responses"
    anthropic_url = f"{fixture_process.handoff['anthropic_base_url']}/v1/messages"
    payload = json.dumps(
        {
            "input": "do not retain this prompt",
            "image": "do not retain this media",
            "coordinates": {"lat": 59.9, "lon": 10.7},
        }
    ).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer fixture-secret"}

    openai_status, openai_body = _request(openai_url, body=payload, headers=headers)
    anthropic_status, anthropic_body = _request(anthropic_url, body=payload, headers=headers)

    assert openai_status == 200
    assert anthropic_status == 200
    openai = json.loads(openai_body)
    anthropic = json.loads(anthropic_body)
    expected_status = (
        "incomplete" if fixture_process.handoff["scenario"] == "partial" else "completed"
    )
    assert openai["status"] == expected_status
    assert anthropic["stop_reason"] == (
        "max_tokens" if expected_status == "incomplete" else "end_turn"
    )

    state = _state(fixture_process)
    assert state["counts"] == {
        "provider_requests": 2,
        "by_path": {"/v1/responses": 1, "/v1/messages": 1},
        "by_scenario": {fixture_process.handoff["scenario"]: 2},
    }
    serialized = json.dumps(state)
    assert "fixture-secret" not in serialized
    assert "do not retain this prompt" not in serialized
    assert "do not retain this media" not in serialized
    assert "59.9" not in serialized
    assert "10.7" not in serialized
    assert state["requests"] == [
        {
            "method": "POST",
            "path": "/v1/responses",
            "request_shape": {
                "body_kind": "json_object",
                "content_type": "application/json",
                "json_keys": ["coordinates", "image", "input"],
            },
        },
        {
            "method": "POST",
            "path": "/v1/messages",
            "request_shape": {
                "body_kind": "json_object",
                "content_type": "application/json",
                "json_keys": ["coordinates", "image", "input"],
            },
        },
    ]


@pytest.mark.parametrize(
    ("format_name", "prompt_items", "expected_key"),
    [
        ("plant_candidates", [], "candidates"),
        ("plant_diagnoses", [], "diagnoses"),
        ("plant_data", [], "latin"),
        ("care_instructions_batch", [{"plt_id": "PLT-FIXTURE"}], "plants"),
        ("task_descriptions_batch", [{"task_key": "task-fixture"}], "tasks"),
    ],
)
def test_openai_structured_format_names_have_stable_compatible_output(
    format_name: str,
    prompt_items: list[dict[str, str]],
    expected_key: str,
) -> None:
    process = FixtureProcess("success")
    try:
        prompt = "Fixture prompt"
        if prompt_items:
            prompt = f"Fixture prompt\n{json.dumps(prompt_items)}"
        body = json.dumps(
            {
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "text": {"format": {"type": "json_schema", "name": format_name, "schema": {}}},
            }
        ).encode()
        status, response_body = _request(
            f"{process.handoff['openai_base_url']}/responses",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        structured = json.loads(_openai_text(response_body))
        assert expected_key in structured
        if format_name == "care_instructions_batch":
            assert structured["plants"][0]["plt_id"] == "PLT-FIXTURE"
        if format_name == "task_descriptions_batch":
            assert structured["tasks"][0]["task_key"] == "task-fixture"
        assert "Fixture prompt" not in json.dumps(_state(process))
    finally:
        process.close()


def test_openai_plain_text_chat_has_the_existing_stable_reply() -> None:
    process = FixtureProcess("success")
    try:
        status, body = _request(
            f"{process.handoff['openai_base_url']}/responses",
            body=b'{"input":"private garden question"}',
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert _openai_text(body) == (
            "Deterministic test reply: Check soil moisture before watering."
        )
    finally:
        process.close()


def test_openai_sdk_accepts_the_fixture_responses_contract() -> None:
    process = FixtureProcess("success")
    try:
        client = OpenAI(
            api_key="fixture-key-not-retained",
            base_url=process.handoff["openai_base_url"],
        )
        response = client.responses.create(
            model="gardenops-loopback-fixture",
            input="private garden question",
        )
        assert response.output_text == (
            "Deterministic test reply: Check soil moisture before watering."
        )
        assert "fixture-key-not-retained" not in json.dumps(_state(process))
        assert "private garden question" not in json.dumps(_state(process))
    finally:
        process.close()


def test_shademap_sdk_success_is_counted_without_retaining_api_key() -> None:
    process = FixtureProcess("success")
    try:
        status, body = _request(
            f"{process.handoff['anthropic_base_url']}/shademap/sdk/load",
            body=b'{"api_key":"shademap-secret-value"}',
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert json.loads(body) == {"fixture": "shademap-sdk-load", "status": "ok"}
        state = _state(process)
        assert state["counts"] == {
            "provider_requests": 1,
            "by_path": {"/shademap/sdk/load": 1},
            "by_scenario": {"success": 1},
        }
        assert state["requests"] == [
            {
                "method": "POST",
                "path": "/shademap/sdk/load",
                "request_shape": {
                    "body_kind": "json_object",
                    "content_type": "application/json",
                    "json_keys": ["api_key"],
                },
            },
        ]
        assert "shademap-secret-value" not in json.dumps(state)
    finally:
        process.close()


def test_cors_scenario_control_switches_provider_behavior_without_payload_retention() -> None:
    process = FixtureProcess("success")
    control_url = f"{process.handoff['anthropic_base_url']}/__fixture__/scenario"
    response_url = f"{process.handoff['openai_base_url']}/responses"
    try:
        options_status, options_headers = _options_headers(control_url)
        assert options_status == 204
        assert options_headers["access-control-allow-origin"] == "*"
        assert "POST" in options_headers["access-control-allow-methods"]

        invalid_status, _ = _request(
            control_url,
            body=b'{"scenario":"not-supported","api_key":"must-not-retain"}',
            headers={"Content-Type": "application/json"},
        )
        assert invalid_status == 400

        quota_status, quota_body = _request(
            control_url,
            body=b'{"scenario":"quota","prompt":"must-not-retain"}',
            headers={"Content-Type": "application/json"},
        )
        assert quota_status == 200
        assert json.loads(quota_body) == {"scenario": "quota"}
        provider_status, _ = _request(
            response_url,
            body=b'{"input":"must-not-retain"}',
            headers={"Content-Type": "application/json"},
        )
        assert provider_status == 429

        success_status, success_body = _request(
            control_url,
            body=b'{"scenario":"success","coordinates":[59.9,10.7]}',
            headers={"Content-Type": "application/json"},
        )
        assert success_status == 200
        assert json.loads(success_body) == {"scenario": "success"}
        provider_status, _ = _request(
            response_url,
            body=b'{"input":"must-not-retain"}',
            headers={"Content-Type": "application/json"},
        )
        assert provider_status == 200

        state = _state(process)
        assert state["scenario"] == "success"
        assert state["counts"] == {
            "provider_requests": 2,
            "by_path": {"/v1/responses": 2},
            "by_scenario": {"success": 1, "quota": 1},
        }
        serialized = json.dumps(state)
        assert "must-not-retain" not in serialized
        assert "59.9" not in serialized
        assert "10.7" not in serialized
    finally:
        process.close()


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("quota", 429), ("unauthorized", 401)],
)
def test_quota_and_unauthorized_return_fixed_vendor_errors(
    scenario: str,
    expected_status: int,
) -> None:
    process = FixtureProcess(scenario)
    try:
        status, body = _request(
            f"{process.handoff['openai_base_url']}/responses",
            body=b'{"input":"private"}',
            headers={"Content-Type": "application/json", "Authorization": "Bearer private-key"},
        )
        assert status == expected_status
        error = json.loads(body)["error"]
        assert error["message"].startswith("Fixture ")
        assert "private" not in json.dumps(_state(process))
    finally:
        process.close()


def test_malformed_response_and_timeout_are_deterministic() -> None:
    malformed = FixtureProcess("malformed")
    timeout = FixtureProcess("timeout", timeout_ms=300)
    try:
        status, body = _request(
            f"{malformed.handoff['openai_base_url']}/responses",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        with pytest.raises(json.JSONDecodeError):
            json.loads(body)
        assert _state(malformed)["counts"]["provider_requests"] == 1

        started = time.monotonic()
        with pytest.raises((TimeoutError, socket.timeout, urllib.error.URLError)):
            _request(
                f"{timeout.handoff['openai_base_url']}/responses",
                body=b"{}",
                timeout=0.05,
                headers={"Content-Type": "application/json"},
            )
        assert time.monotonic() - started < 0.25
        assert _state(timeout)["counts"]["provider_requests"] == 1
    finally:
        malformed.close()
        timeout.close()


def test_handoff_is_loopback_only_and_sigterm_closes_the_listener(tmp_path: Path) -> None:
    ready_file = tmp_path / "fixture-ready.json"
    process = subprocess.Popen(
        ["node", str(FIXTURE), "--scenario", "success", "--ready-file", str(ready_file)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready_line = process.stdout.readline().strip()
    assert ready_line.startswith(READY_PREFIX)
    handoff = json.loads(ready_line.removeprefix(READY_PREFIX))
    assert handoff == json.loads(ready_file.read_text(encoding="utf-8"))
    assert handoff["host"] == "127.0.0.1"
    assert handoff["env"]["OPENAI_BASE_URL"] == handoff["openai_base_url"]
    assert handoff["env"]["ANTHROPIC_BASE_URL"] == handoff["anthropic_base_url"]
    assert handoff["env"]["GARDENOPS_E2E_PROVIDER_URL"] == handoff["openai_base_url"]
    assert handoff["env"]["GARDENOPS_PROVIDER_FIXTURE_CONTROL_URL"] == handoff["control_url"]
    for raw_url in (
        handoff["openai_base_url"],
        handoff["anthropic_base_url"],
        handoff["shademap_sdk_load_url"],
        handoff["state_url"],
        handoff["control_url"],
    ):
        _assert_loopback_url(raw_url)

    port = handoff["port"]
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=3) == 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        assert connection.connect_ex(("127.0.0.1", port)) != 0


def test_non_loopback_host_override_is_rejected() -> None:
    result = subprocess.run(
        ["node", str(FIXTURE), "--host", "0.0.0.0"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "fixed to 127.0.0.1" in result.stderr
