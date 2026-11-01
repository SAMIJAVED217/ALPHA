import httpx
import pytest

from alpha.tools.catalogue import build_toolbox
from alpha.tools.grok import API, MODELS, STAND_IN, GrokClient, choose_model, grok_tools
from alpha.tools.registry import ToolRefused

OFFERED = {"data": [{"id": "grok-test"}]}


def client_answering(status=200, body=None, seen=None, offered=OFFERED):
    def reply(request):
        if seen is not None:
            seen.append(request)
        if str(request.url) == MODELS and status == 200:
            return httpx.Response(200, json=offered)
        return httpx.Response(status, json=body if body is not None else {})

    return GrokClient(lambda: "test-key", httpx.MockTransport(reply))


def test_a_question_reaches_xai_with_the_key_and_returns_the_answer():
    seen = []
    body = {"choices": [{"message": {"content": "Forty two."}}]}
    answer = client_answering(body=body, seen=seen).ask("What is the answer?")
    assert answer == "Forty two."
    assert str(seen[-1].url) == API
    assert seen[-1].headers["authorization"] == "Bearer test-key"
    assert b"What is the answer?" in seen[-1].content
    assert b"grok-test" in seen[-1].content


def test_the_model_is_asked_for_once_and_then_remembered():
    seen = []
    client = client_answering(body={"choices": [{"message": {"content": "Hi"}}]}, seen=seen)
    client.ask("one")
    client.ask("two")
    assert [str(request.url) for request in seen] == [MODELS, API, API]


@pytest.mark.parametrize(
    "offered, best",
    [
        (["grok-3", "grok-4", "grok-4-1"], "grok-4-1"),
        (["grok-4.5", "grok-4-fast", "grok-4-mini"], "grok-4.5"),
        (["grok-2-1212", "grok-3"], "grok-3"),
        (["grok-4", "grok-4-heavy"], "grok-4"),
        (["grok-5-image", "grok-5-vision", "grok-3"], "grok-3"),
    ],
)
def test_the_newest_full_chat_model_is_chosen(offered, best):
    assert choose_model(offered) == best


@pytest.mark.parametrize("offered", [[], ["grok-2-image"], ["other-model"]])
def test_having_no_chat_model_is_reported(offered):
    with pytest.raises(ToolRefused, match="chat model"):
        choose_model(offered)


def test_an_account_without_credits_is_told_so_rather_than_blaming_the_key():
    client = GrokClient(
        lambda: "k",
        httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "This team has no credits yet"})),
    )
    with pytest.raises(ToolRefused, match="no credits"):
        client.ask("hello")


def test_standing_in_tells_grok_it_cannot_act():
    seen = []
    client_answering(body={"choices": [{"message": {"content": "Hi"}}]}, seen=seen).stand_in("hello")
    assert STAND_IN.encode() in seen[-1].content


def test_connected_only_means_a_key_is_saved():
    assert GrokClient(lambda: "k").connected()
    assert not GrokClient(lambda: "").connected()


def test_without_a_key_it_says_how_to_connect_and_sends_nothing():
    seen = []
    client = GrokClient(lambda: "", httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    with pytest.raises(ToolRefused, match="Connections"):
        client.ask("hello")
    assert seen == []


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_errors_are_reported_plainly(status):
    with pytest.raises(ToolRefused):
        client_answering(status=status).ask("hello")


def test_a_strange_reply_is_refused():
    with pytest.raises(ToolRefused):
        client_answering(body={"nope": 1}).ask("hello")


def test_a_dropped_connection_is_refused():
    def fail(request):
        raise httpx.ConnectError("no")

    with pytest.raises(ToolRefused):
        GrokClient(lambda: "k", httpx.MockTransport(fail)).ask("hello")


def test_grok_is_offered_and_its_answers_count_as_outside_text():
    tool = build_toolbox(None).get("ask_grok")
    assert tool.reads_outside and not tool.needs_confirmation


def test_the_tool_runs_a_question_and_limits_its_length():
    tool = grok_tools(client_answering(body={"choices": [{"message": {"content": "Hi"}}]}))[0]
    assert tool.run({"question": "hello"})["answer"] == "Hi"
    with pytest.raises(ToolRefused):
        tool.run({"question": "x" * 9000})
