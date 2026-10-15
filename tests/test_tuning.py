import json
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from support_agent.config import Settings, get_settings
from support_agent.models import DocumentChunk, SourceDocument
from support_agent.services.agent import SupportAgent, conversation_context
from support_agent.services.cache import get_retrieval_cache
from support_agent.services.documents import PageText, chunk_pages
from support_agent.services.rag import RAGService, SearchHit
from support_agent.tuning import BASE_CONFIG, read_tuning
from support_agent.tuning_management import migrate_env


def test_configuration_priority_and_snapshot(tmp_path, monkeypatch):
    normal = tmp_path / "normal.toml"
    normal.write_text("[retrieval]\nretrieval_top_k=7\n")
    experiment = tmp_path / "experiment.toml"
    experiment.write_text("[retrieval]\nretrieval_top_k=9\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("RETRIEVAL_TOP_K=8\nLLM_API_KEY=private-test-secret\n")
    kwargs = {"_env_file": dotenv, "agent_config_file": str(normal)}
    assert Settings(_env_file=None, agent_config_file=str(normal)).retrieval_top_k == 7
    assert Settings(**kwargs).retrieval_top_k == 8
    kwargs["agent_experiment_file"] = str(experiment)
    assert Settings(**kwargs).retrieval_top_k == 9
    monkeypatch.setenv("RETRIEVAL_TOP_K", "10")
    assert Settings(**kwargs).retrieval_top_k == 10
    explicit = Settings(**kwargs, retrieval_top_k=11)
    assert explicit.retrieval_top_k == 11
    assert "private-test-secret" not in json.dumps(explicit.tuning_snapshot())
    assert (
        Settings(**kwargs).tuning_snapshot()["fingerprint"]
        != explicit.tuning_snapshot()["fingerprint"]
    )


@pytest.mark.parametrize(
    "value",
    [
        "[unknown]\nretrieval_top_k=3",
        "[generation]\nllm_api_key='secret'",
        "[retrieval]\nretrieval_top_k=3\n[evidence]\nretrieval_top_k=4",
        "retrieval_top_k=3",
    ],
)
def test_unknown_or_unsafe_configuration_rejected(tmp_path, value):
    path = tmp_path / "bad.toml"
    path.write_text(value)
    with pytest.raises(ValueError):
        Settings(_env_file=None, agent_config_file=str(path))


@pytest.mark.parametrize(
    "values",
    [
        {"chunk_size": 100, "chunk_overlap": 100},
        {"retrieval_lexical_weight": 0.6},
        {"evidence_total_chars": 100},
        {"chat_history_limit": -1},
        {"embedding_batch_size": 0},
        {"llm_temperature": float("inf")},
    ],
)
def test_invalid_parameters_fail_before_runtime(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_missing_experiment_and_working_directory_overlay(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path(Settings().tuning_snapshot()["config_file"]) == BASE_CONFIG
    (tmp_path / "config").mkdir()
    (tmp_path / "config/agent.toml").write_text("[chunking]\nchunk_size=320\nchunk_overlap=40")
    assert Settings().chunk_size == 320
    with pytest.raises(FileNotFoundError):
        Settings(agent_experiment_file=str(tmp_path / "absent.toml"))


def make_hit(identifier, document_id=1, score=1.0, text="长资料" * 200):
    return SearchHit(
        DocumentChunk(id=str(identifier), content=text, chunk_metadata={}),
        SourceDocument(id=str(document_id), filename=f"{document_id}.md"),
        score,
    )


def test_custom_evidence_history_and_chunking_budgets(monkeypatch):
    settings = Settings(
        _env_file=None,
        evidence_top_k=2,
        evidence_per_document=1,
        evidence_total_chars=90,
        evidence_snippet_chars=50,
        followup_message_limit=1,
        followup_user_chars=10,
        followup_query_chars=18,
        chunk_size=30,
        chunk_overlap=5,
        citation_excerpt_chars=12,
    )
    hits = [make_hit(1), make_hit(2, score=0.9), make_hit(3, 2, score=0.8)]
    assert [h.chunk.id for h in SupportAgent.evidence_hits(hits, settings)] == ["1", "3"]
    contexts = SupportAgent.evidence_contexts("问题", hits, settings)
    assert sum(map(len, contexts)) <= 90
    assert all(len(c) <= 50 for c in contexts)
    assert len(RAGService.citations(hits, settings)[0].excerpt) == 12
    retrieval, question = conversation_context(
        "下一步呢",
        [
            {"role": "user", "content": "应该被省略的旧事实"},
            {"role": "user", "content": "刚检查过平台通信状态现在正常"},
        ],
        settings,
    )
    assert len(retrieval) <= 18
    # The original issue is kept as a separate ledger; only recent descriptions are capped here.
    recent_question = question.split("此前用户描述与反馈", 1)[1]
    assert "应该被省略" not in recent_question
    monkeypatch.setenv("CHUNK_SIZE", "30")
    monkeypatch.setenv("CHUNK_OVERLAP", "5")
    get_settings.cache_clear()
    pieces = chunk_pages([PageText(page=1, text="a" * 60)])
    assert [len(p.text) for p in pieces] == [30, 30, 10]


async def test_changed_retrieval_weights_do_not_reuse_cached_scores(db_session):
    baseline = Settings(_env_file=None, embedding_backend="hash")
    rag = RAGService(db_session, settings=baseline)
    await rag.ingest("test.md", "text/markdown", "逆变器通信中断 检查RS485通信接线".encode())
    first = await rag.search("逆变器通信中断")
    assert first
    altered = Settings(
        _env_file=None,
        embedding_backend="hash",
        retrieval_lexical_weight=1,
        retrieval_phrase_weight=0,
        retrieval_semantic_weight=0,
        retrieval_coverage_sharpness=1,
    )
    other = RAGService(db_session, settings=altered, cache=get_retrieval_cache())
    second = await other.search("逆变器通信中断")
    assert second and first[0].score != second[0].score


def test_legacy_migration_preserves_effective_parameters_and_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config/agent.toml"
    config.parent.mkdir()
    config.write_text(BASE_CONFIG.read_text("utf-8"), "utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# keep this\nLLM_API_KEY=local-secret-only\nCHUNK_SIZE=333\n"
        "CHUNK_OVERLAP=44\nRETRIEVAL_TOP_K=7\n",
        "utf-8",
    )
    before = Settings(_env_file=dotenv).tuning_snapshot()["parameters"]
    result = migrate_env(dotenv, config)
    assert len(result["migrated"]) == 3
    assert Settings(_env_file=dotenv).tuning_snapshot()["parameters"] == before
    assert "LLM_API_KEY=local-secret-only" in dotenv.read_text()
    assert "CHUNK_SIZE" not in dotenv.read_text()
    assert "local-secret-only" not in config.read_text("utf-8")
    assert (Path(result["backup_dir"]) / ".env.bak").exists()
    assert migrate_env(dotenv, config)["migrated"] == []


def test_migration_failure_restores_original_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "agent.toml"
    config.write_text(BASE_CONFIG.read_text("utf-8"), "utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CHUNK_SIZE=333\nCHUNK_OVERLAP=44\n")
    before = config.read_bytes(), dotenv.read_bytes()
    import support_agent.tuning_management as management

    real_settings = management.Settings
    calls = 0

    def fail_after_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic failure")
        return real_settings(*args, **kwargs)

    monkeypatch.setattr(management, "Settings", fail_after_write)
    with pytest.raises(RuntimeError):
        migrate_env(dotenv, config)
    assert (config.read_bytes(), dotenv.read_bytes()) == before


def test_distribution_has_tuning_baseline_and_preserves_local_overrides(tmp_path, monkeypatch):
    installer = runpy.run_path(str(Path(__file__).parents[1] / "scripts/manage_install.py"))
    import hashlib

    from tests.test_installer import package_at

    def package(version):
        folder = package_at(tmp_path / version, version)
        manifest = json.loads((folder / "manifest.json").read_text())
        for name, data in {
            "config/agent.toml": BASE_CONFIG.read_bytes(),
            "scripts/desktop_server.py": b"synthetic",
        }.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
        (folder / "manifest.json").write_text(json.dumps(manifest))
        return folder

    monkeypatch.setattr(installer["subprocess"], "run", lambda *a, **kw: None)
    target = tmp_path / "installed"
    installer["install"](package("1.16.0"), target, shortcuts=False)
    config = target / "data/config/agent.toml"
    assert read_tuning(config)["chunk_size"] == 700
    config.write_text("[retrieval]\nretrieval_top_k=8\n")
    installer["install"](package("1.16.1"), target, shortcuts=False)
    assert read_tuning(config) == {"retrieval_top_k": 8}


async def test_generation_parameters_reach_model_request():
    from unittest.mock import patch

    import httpx

    from support_agent.services.llm import OpenAICompatibleClient

    settings = Settings(
        _env_file=None,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="dummy",
        llm_model="test",
        allow_remote_llm=True,
        llm_temperature=0.6,
        llm_max_output_tokens=789,
    )
    with patch("support_agent.services.llm.httpx.AsyncClient") as client_class:
        post = client_class.return_value.__aenter__.return_value.post
        post.return_value = httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.invalid"),
            json={"choices": [{"message": {"content": "test"}}]},
        )
        await OpenAICompatibleClient(settings).answer("question", [])
    payload = post.await_args.kwargs["json"]
    assert payload["temperature"] == 0.6
    assert payload["max_tokens"] == 789


def test_embedding_instances_are_separated_by_encoder_parameters(monkeypatch):
    from support_agent.services import semantic

    calls = []

    def build(settings):
        result = object()
        calls.append(result)
        return result

    monkeypatch.setattr(semantic, "_BACKENDS", {})
    monkeypatch.setattr(semantic, "build_embedding_backend", build)
    first = Settings(_env_file=None, embedding_backend="hash")
    second = Settings(_env_file=None, embedding_backend="hash", embedding_max_length=256)
    assert semantic.get_embedding_backend(first) is semantic.get_embedding_backend(first)
    assert semantic.get_embedding_backend(first) is not semantic.get_embedding_backend(second)
    assert len(calls) == 2


async def test_zero_history_disables_root_message_retention():
    from unittest.mock import AsyncMock

    db = AsyncMock()
    agent = SupportAgent(db, Settings(_env_file=None, chat_history_limit=0))
    assert await agent._history("unused") == []
    db.scalars.assert_not_awaited()
    db.scalar.assert_not_awaited()


def test_wheel_layout_finds_packaged_baseline(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    package = tmp_path / "site/support_agent"
    (package / "defaults").mkdir(parents=True)
    for filename in ("__init__.py", "config.py", "tuning.py"):
        shutil.copy2(root / "src/support_agent" / filename, package / filename)
    shutil.copy2(BASE_CONFIG, package / "defaults/agent.toml")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(tmp_path / "site"), str(root / ".localdeps"), str(root / ".testdeps")]
        ),
    }
    code = (
        "from support_agent.config import Settings; "
        "s=Settings(_env_file=None); assert s.chunk_size==700; "
        "assert 'defaults' in s.tuning_snapshot()['base_file']"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
