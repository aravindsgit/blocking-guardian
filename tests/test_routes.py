def open_case(client, scenario="idle_blocker", mode="approval"):
    response = client.post("/cases", data={"scenario": scenario, "mode": mode, "seed": "7"}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def test_health_needs_no_login(anon):
    response = anon.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_pages_need_login(anon):
    assert anon.get("/").status_code == 401
    assert anon.post("/cases", data={"scenario": "idle_blocker", "mode": "approval"}).status_code == 401
    assert anon.get("/", auth=("admin", "wrong")).status_code == 401


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200 and "Open a blocking-session case" in response.text


def test_open_and_view_a_case(client):
    location = open_case(client)
    assert location == "/cases/1"
    page = client.get(location)
    assert page.status_code == 200
    assert "Root session 210" in page.text and "R6" in page.text and "Approve: end session 210" in page.text


def test_invalid_form_shows_errors(client):
    response = client.post("/cases", data={"scenario": "nope", "mode": "chaos", "seed": "abc"})
    assert response.status_code == 422
    assert "Choose one of the listed scenarios" in response.text and "whole number" in response.text


def test_approving_clears_the_chain_and_is_audited(client):
    location = open_case(client)
    assert client.post(f"{location}/approve/210", follow_redirects=False).status_code == 303
    page = client.get(location).text
    assert "No blocking sessions remain." in page and "Approved: ended root session 210" in page


def test_the_policy_refuses_to_end_a_protected_or_unsafe_session(client):
    location = open_case(client, "batch_blocker")
    assert client.post(f"{location}/approve/305").status_code == 409
    assert "Root session 305" in client.get(location).text
    location = open_case(client, "short_wait")
    assert client.post(f"{location}/approve/510").status_code == 409


def test_unknown_chain_is_a_404(client):
    location = open_case(client)
    assert client.post(f"{location}/approve/9999").status_code == 404


def test_rejecting_leaves_the_session_alone(client):
    location = open_case(client)
    client.post(f"{location}/reject/210")
    page = client.get(location).text
    assert "Rejected by a human" in page and "nothing was done" in page
    assert client.post(f"{location}/approve/210").status_code == 409


def test_auto_mode_acts_by_itself_only_when_safe(client):
    page = client.get(open_case(client, "idle_blocker", "auto")).text
    assert "No blocking sessions remain." in page and "Ended root session 210 under policy" in page
    page = client.get(open_case(client, "mass_block", "auto")).text
    assert "Root session 410" in page and "approval required" in page


def test_switching_to_auto_mode_runs_pending_safe_actions(client):
    location = open_case(client, "idle_blocker", "approval")
    assert "Root session 210" in client.get(location).text
    client.post(f"{location}/mode", data={"mode": "auto"})
    assert "No blocking sessions remain." in client.get(location).text


def test_advancing_time_escalates_a_short_wait(client):
    location = open_case(client, "short_wait")
    assert "keep waiting" in client.get(location).text
    client.post(f"{location}/advance")
    assert "end the idle root session" in client.get(location).text


def test_ai_review_without_a_key_explains_why(client):
    location = open_case(client)
    page = client.post(f"{location}/review/210")
    assert page.status_code == 200 and "ANTHROPIC_API_KEY" in page.text


def test_ai_concern_forces_human_approval_even_in_auto_mode(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.llm.call_anthropic",
                        lambda system, user: '{"agree": false, "concern": "Session may hold a critical lock."}')
    location = open_case(client, "idle_blocker", "approval")
    client.post(f"{location}/review/210")
    page = client.get(location).text
    assert "raised a concern" in page and "approval required" in page
    client.post(f"{location}/mode", data={"mode": "auto"})
    assert "Root session 210" in client.get(location).text


def test_ticket_download(client):
    location = open_case(client)
    response = client.get(f"{location}/ticket.txt")
    assert response.status_code == 200 and "text/plain" in response.headers["content-type"]
    assert "Root session 210" in response.text


def test_cross_site_post_is_blocked(client):
    response = client.post("/cases", data={"scenario": "idle_blocker", "mode": "approval"},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_delete_a_case(client):
    location = open_case(client)
    assert client.post(f"{location}/delete").status_code == 200
    assert client.get(location).status_code == 404
