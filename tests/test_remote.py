import httpx
import pytest

from alpha import remote
from alpha.remote import CODE_SECONDS, MOST_WRONG_CODES, SEEN_SECONDS, RemoteControl, RemoteServer, allowed_peer


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    said, stopped, clock = [], [], Clock()
    control = RemoteControl(said.append, lambda: stopped.append(True), clock)
    server = RemoteServer(control, "127.0.0.1", 0)
    server.start()
    browser = httpx.Client(base_url=f"http://127.0.0.1:{server.port}")
    yield browser, control, server, said, stopped, clock
    browser.close()
    server.stop()


def join(browser, control):
    answer = browser.post("/pair", json={"code": control.new_code()})
    assert answer.status_code == 200
    return answer


def test_the_page_opens_but_tells_nothing_before_joining(rig):
    browser, control, *_ = rig
    control.new_code()
    page = browser.get("/")
    assert page.status_code == 200
    assert control._code not in page.text
    assert browser.get("/events").status_code == 401


def test_a_message_needs_a_joined_device(rig):
    browser, _, _, said, *_ = rig
    assert browser.post("/send", json={"text": "hello"}).status_code == 401
    assert said == []


def test_a_joined_device_sends_messages_stops_replies_and_sees_answers(rig):
    browser, control, _, said, stopped, _ = rig
    join(browser, control)
    assert browser.post("/send", json={"text": "  hello  "}).status_code == 200
    assert said == ["hello"]
    assert browser.post("/stop", json={}).status_code == 200
    assert stopped == [True]
    control.push("reply", "Hi there")
    events = browser.get("/events?after=0").json()["events"]
    assert [event["text"] for event in events] == ["Hi there"]
    assert browser.get(f"/events?after={events[0]['id']}").json()["events"] == []


def test_the_token_is_kept_away_from_page_scripts_and_never_stored_plainly(rig):
    browser, control, *_ = rig
    answer = join(browser, control)
    cookie = answer.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    token = cookie.split("=", 1)[1].split(";")[0]
    assert token not in control._devices


def test_a_code_works_once(rig):
    browser, control, *_ = rig
    code = control.new_code()
    assert browser.post("/pair", json={"code": code}).status_code == 200
    assert httpx.post(f"{browser.base_url}/pair", json={"code": code}).status_code == 403


def test_a_code_runs_out(rig):
    browser, control, *_, clock = rig
    code = control.new_code()
    clock.now += CODE_SECONDS + 1
    assert browser.post("/pair", json={"code": code}).status_code == 403


def test_too_many_wrong_codes_cancel_the_code(rig):
    browser, control, *_ = rig
    code = control.new_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MOST_WRONG_CODES):
        assert browser.post("/pair", json={"code": wrong}).status_code == 403
    assert browser.post("/pair", json={"code": code}).status_code == 403


def test_turning_it_off_forgets_every_device(rig):
    browser, control, *_ = rig
    join(browser, control)
    control.forget_everyone()
    assert browser.get("/events").status_code == 401


def test_nothing_on_the_page_or_server_can_confirm_an_action(rig):
    browser, control, *_ = rig
    join(browser, control)
    assert browser.post("/confirm", json={}).status_code == 404
    assert "confirm" not in browser.get("/").text.lower().replace("press confirm", "")


@pytest.mark.parametrize(
    "address, allowed",
    [("127.0.0.1", True), ("192.168.1.20", True), ("10.0.0.5", True), ("100.101.102.103", True),
     ("8.8.8.8", False), ("1.1.1.1", False), ("nonsense", False)],
)
def test_only_private_networks_are_accepted(address, allowed):
    assert allowed_peer(address) is allowed


def test_requests_from_another_network_are_refused(rig, monkeypatch):
    browser, *_ = rig
    monkeypatch.setattr(remote, "allowed_peer", lambda address: False)
    assert browser.get("/").status_code == 403


def test_a_website_pretending_to_be_this_address_is_refused(rig):
    browser, *_ = rig
    assert browser.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_another_websites_page_cannot_post_here(rig):
    browser, control, _, said, *_ = rig
    join(browser, control)
    answer = browser.post("/send", json={"text": "hi"}, headers={"Origin": "https://evil.example"})
    assert answer.status_code == 403
    assert said == []


def test_only_json_is_accepted(rig):
    browser, control, *_ = rig
    join(browser, control)
    assert browser.post("/send", content="text=hi", headers={"Content-Type": "text/plain"}).status_code == 400


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001, 5, None])
def test_bad_messages_are_refused(rig, text):
    browser, control, _, said, *_ = rig
    join(browser, control)
    assert browser.post("/send", json={"text": text}).status_code == 400
    assert said == []


def test_a_huge_request_is_refused(rig):
    browser, control, *_ = rig
    join(browser, control)
    assert browser.post("/send", json={"text": "x" * 20_000}).status_code == 400


def test_connected_fades_when_the_device_stops_asking(rig):
    browser, control, *_, clock = rig
    assert not control.connected()
    join(browser, control)
    browser.get("/events")
    assert control.connected()
    clock.now += SEEN_SECONDS + 1
    assert not control.connected()


def test_nothing_is_kept_for_the_phone_until_a_device_has_joined(rig):
    _, control, *_ = rig
    control.push("reply", "private")
    assert control.events_after(0) == []
