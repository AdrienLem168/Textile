"""Tests du workflow de veille déchets textiles."""

import json

import pytest

from worker.workflows import textile_report as tr


def test_workflow_is_discovered_and_named():
    import mistralai.workflows as workflows

    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = [cls.__dict__["__workflows_workflow_def"].name for cls in classes]
    assert "textile-waste-weekly-report" in names


def test_parse_json_array_plain():
    assert tr._parse_json_array('[{"title": "A"}]') == [{"title": "A"}]


def test_parse_json_array_in_code_fence():
    raw = "```json\n[{\"title\": \"A\"}]\n```"
    assert tr._parse_json_array(raw) == [{"title": "A"}]


def test_parse_json_array_with_surrounding_text():
    raw = "Voici les résultats :\n[{\"title\": \"A\"}, {\"title\": \"B\"}]\nFin."
    assert len(tr._parse_json_array(raw)) == 2


def test_parse_json_array_empty():
    assert tr._parse_json_array("[]") == []
    assert tr._parse_json_array("aucun résultat") == []


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeChat:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def complete_async(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.content)


class FakeDocuments:
    def __init__(self, document_id):
        self.document_id = document_id

    async def upload_async(self, **kwargs):
        self.kwargs = kwargs

        class Doc:
            id = self.document_id

        return Doc()


class FakeLibraries:
    def __init__(self, document_id):
        self.documents = FakeDocuments(document_id)


class FakeBeta:
    def __init__(self, document_id):
        self.libraries = FakeLibraries(document_id)


class FakeClient:
    def __init__(self, chat_content, document_id="doc-1"):
        self.chat = FakeChat(chat_content)
        self.beta = FakeBeta(document_id)


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(chat_content, document_id="doc-1"):
        client = FakeClient(chat_content, document_id)
        monkeypatch.setattr(tr, "get_mistral_client", lambda: client)
        return client

    return _patch


async def test_research_scope_returns_items(patch_client):
    items = [{"title": "Nouvelle usine de recyclage", "url": "https://example.org"}]
    client = patch_client(json.dumps(items))

    result = await tr.research_scope("france", "2026-02-02", "2026-02-09")

    assert result == items
    call = client.chat.calls[0]
    assert call["model"] == tr.RESEARCH_MODEL
    assert any(isinstance(t, tr.WebSearchTool) for t in call["tools"])
    assert "france" in call["messages"][0]["content"]


async def test_research_scope_unknown_scope():
    with pytest.raises(ValueError):
        await tr.research_scope("bretagne", "2026-02-02", "2026-02-09")


async def test_synthesize_report(patch_client):
    client = patch_client("# Veille déchets textiles\n\nRapport complet.")
    items = [{"title": "A", "scope": "france", "summary": "s"}]

    report = await tr.synthesize_report(items, "2026-02-02", "2026-02-09")

    assert report.startswith("# Veille déchets textiles")
    assert "2026-02-02" in client.chat.calls[0]["messages"][0]["content"]


async def test_publish_report_without_library(monkeypatch):
    monkeypatch.delenv("REPORT_LIBRARY_ID", raising=False)
    assert await tr.publish_report("# rapport", "rapport.md", None) is None


async def test_publish_report_uploads_markdown(patch_client, monkeypatch):
    patch_client("unused", document_id="doc-42")
    monkeypatch.setattr(tr.os.environ, "get", lambda k, d=None: None)

    document_id = await tr.publish_report("# rapport", "rapport.md", "lib-1")

    assert document_id == "doc-42"


class FakeOutlookClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))


async def test_send_report_email_calls_outlook():
    client = FakeOutlookClient()

    await tr.send_report_email.__original_func__(
        "dest@example.org", "Sujet", "# rapport", outlook=client
    )

    assert len(client.calls) == 1
    tool_name, arguments = client.calls[0]
    assert tool_name == tr.EMAIL_TOOL_NAME
    assert arguments["to"] == ["dest@example.org"]
    assert arguments["subject"] == "Sujet"
    assert arguments["body"] == "# rapport"


async def test_email_arguments_shape():
    arguments = tr._email_arguments("a@b.fr", "s", "md")

    assert set(arguments) == {"to", "subject", "body", "body_content_type"}
    assert arguments["to"] == ["a@b.fr"]
    assert arguments["body_content_type"] == "markdown"


def test_research_prompt_uses_categories():
    for category in tr.CATEGORIES:
        assert category in tr.RESEARCH_PROMPT
    assert "Marché & filière" in tr.SYNTHESIS_PROMPT
    assert "Réglementation" in tr.SYNTHESIS_PROMPT
    assert "Innovation & R&D" in tr.SYNTHESIS_PROMPT
    assert "Appels à projets & événements" in tr.SYNTHESIS_PROMPT
