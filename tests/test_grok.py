import httpx
import pytest

from alpha.tools.catalogue import build_toolbox
from alpha.tools.grok import (
    API,
    GROQ_ROOT,
    MODELS,
    OPENAI_ROOT,
    STAND_IN,
    GrokClient,
    SecondAI,
    choose_groq_model,
    choose_model,
    choose_openai_model,
    grok_tools,
    groq_client,
    openai_client,
)
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


def test_checking_a_key_lists_models_and_asks_nothing():
    seen = []
    client_answering(seen=seen).check()
    assert [str(request.url) for request in seen] == [MODELS]


def test_checking_a_bad_key_says_so():
    with pytest.raises(ToolRefused, match="refused the key"):
        client_answering(status=401).check()


def answering_groq(seen=None, status=200):
    def reply(request):
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "whisper-large"}, {"id": "llama-test-versatile"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "From Groq"}}]})

    return groq_client(lambda: "gsk-test", httpx.MockTransport(reply))


def test_groq_uses_its_own_address_and_a_chat_model():
    seen = []
    assert answering_groq(seen).ask("hello") == "From Groq"
    assert str(seen[-1].url) == f"{GROQ_ROOT}/chat/completions"
    assert b"llama-test-versatile" in seen[-1].content


@pytest.mark.parametrize(
    "offered, best",
    [(["whisper-large", "llama-a-instant", "llama-b-versatile"], "llama-b-versatile"),
     (["groq/compound", "openai/gpt-oss"], "openai/gpt-oss")],
)
def test_groqs_general_chat_model_is_chosen(offered, best):
    assert choose_groq_model(offered) == best


def test_groq_with_no_chat_model_is_reported():
    with pytest.raises(ToolRefused, match="chat model"):
        choose_groq_model(["whisper-large"])


def test_groqs_free_limit_is_explained():
    with pytest.raises(ToolRefused, match="usage limit"):
        answering_groq(status=429).check()


def test_xai_is_tried_first_when_both_keys_are_saved():
    grok = client_answering(body={"choices": [{"message": {"content": "From Grok"}}]})
    second = SecondAI(grok, answering_groq())
    assert second.ask("hello") == "From Grok" and second.name == "Grok"


def test_groq_is_used_when_only_its_key_is_saved():
    second = SecondAI(GrokClient(lambda: ""), answering_groq())
    assert second.name == "Groq" and second.connected()
    assert second.ask("hello") == "From Groq"


def bad_key_client():
    def reply(request):
        return httpx.Response(400, json={"error": "Incorrect API key provided"})

    return GrokClient(lambda: "wrong", httpx.MockTransport(reply))


def test_a_refused_xai_key_falls_through_to_groq_and_names_it():
    second = SecondAI(bad_key_client(), answering_groq())
    assert second.ask("hello") == "From Groq" and second.name == "Groq"


def test_with_both_refused_the_last_reason_is_shown():
    second = SecondAI(bad_key_client(), answering_groq(status=401))
    with pytest.raises(ToolRefused, match="Groq refused"):
        second.ask("hello")


def test_a_bad_key_says_so_rather_than_showing_error_400():
    with pytest.raises(ToolRefused, match="refused the key"):
        bad_key_client().check()


def test_with_neither_key_nothing_is_connected():
    assert not SecondAI(GrokClient(lambda: ""), groq_client(lambda: "")).connected()


def answering_openai(seen=None, status=200, body=None):
    def reply(request):
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json=body or {})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-test-9"}, {"id": "gpt-9-mini"}, {"id": "whisper-1"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "From OpenAI"}}]})

    return openai_client(lambda: "sk-test", httpx.MockTransport(reply))


def test_openai_uses_its_own_address_and_the_cheaper_model():
    seen = []
    assert answering_openai(seen).ask("hello") == "From OpenAI"
    assert str(seen[-1].url) == f"{OPENAI_ROOT}/chat/completions"
    assert b"gpt-9-mini" in seen[-1].content
    assert seen[-1].headers["authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    "offered, best",
    [
        (["gpt-5", "gpt-5-mini", "gpt-4.1-mini", "gpt-5-nano"], "gpt-5-mini"),
        (["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"], "gpt-4.1-mini"),
        (["gpt-5-mini-2025-08-07", "gpt-5-mini"], "gpt-5-mini"),
        (["gpt-4.1", "gpt-4o"], "gpt-4.1"),
        (["gpt-5-pro", "gpt-5"], "gpt-5"),
        (["gpt-4o-mini-realtime-preview", "gpt-4o-mini-tts", "gpt-4o-mini-search-preview", "gpt-4o"], "gpt-4o"),
    ],
)
def test_openais_mini_chat_model_is_preferred(offered, best):
    assert choose_openai_model(offered) == best


@pytest.mark.parametrize("offered", [[], ["whisper-1", "dall-e-3", "text-embedding-3-large"], ["gpt-image-1"]])
def test_openai_with_no_chat_model_is_reported(offered):
    with pytest.raises(ToolRefused, match="chat model"):
        choose_openai_model(offered)


def test_an_empty_openai_balance_is_told_so():
    body = {"error": {"message": "You exceeded your current quota, please check your plan.", "code": "insufficient_quota"}}
    with pytest.raises(ToolRefused, match="no credits"):
        answering_openai(status=429, body=body).ask("hello")


def test_a_groq_rate_limit_is_not_mistaken_for_an_empty_balance():
    body = {"error": {"message": "Rate limit reached. Upgrade at https://console.groq.com/settings/billing"}}
    client = groq_client(lambda: "k", httpx.MockTransport(lambda r: httpx.Response(429, json=body)))
    with pytest.raises(ToolRefused, match="usage limit"):
        client.ask("hello")


def test_openai_is_tried_first_when_every_key_is_saved():
    grok = client_answering(body={"choices": [{"message": {"content": "From Grok"}}]})
    second = SecondAI(grok, answering_groq(), answering_openai())
    assert second.ask("hello") == "From OpenAI" and second.name == "OpenAI"


def test_an_empty_openai_balance_falls_through_to_the_next():
    body = {"error": {"code": "insufficient_quota"}}
    second = SecondAI(GrokClient(lambda: ""), answering_groq(), answering_openai(status=429, body=body))
    assert second.ask("hello") == "From Groq" and second.name == "Groq"


def test_openai_alone_counts_as_connected():
    assert SecondAI(GrokClient(lambda: ""), groq_client(lambda: ""), answering_openai()).connected()


def test_without_an_openai_key_the_old_order_stays():
    second = SecondAI(client_answering(body={"choices": [{"message": {"content": "From Grok"}}]}), answering_groq(),
                      openai_client(lambda: ""))
    assert second.ask("hello") == "From Grok"


def test_the_answer_says_truthfully_which_ai_gave_it():
    tool = grok_tools(SecondAI(GrokClient(lambda: ""), answering_groq()))[0]
    result = tool.run({"question": "hello"})
    assert result["from"] == "Groq" and result["detail"] == "Groq answered."


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


def test_grok_is_offered_and_runs_at_once():
    tool = build_toolbox(None).get("ask_grok")
    assert not tool.needs_confirmation


def test_the_tool_runs_a_question_and_limits_its_length():
    tool = grok_tools(client_answering(body={"choices": [{"message": {"content": "Hi"}}]}))[0]
    assert tool.run({"question": "hello"})["answer"] == "Hi"
    with pytest.raises(ToolRefused):
        tool.run({"question": "x" * 9000})
