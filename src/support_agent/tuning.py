"""Algorithm parameter schema and strict, non-secret TOML configuration loading."""

import hashlib
import json
import math
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = ROOT / "config/agent.toml"
if not BASE_CONFIG.exists():  # Installed wheel keeps its baseline inside the package.
    BASE_CONFIG = Path(__file__).resolve().parent / "defaults/agent.toml"
SECTIONS = {
    "chunking",
    "retrieval",
    "evidence",
    "history",
    "embedding",
    "generation",
    "versions",
    "quiz",
}


class TuningParameters(BaseModel):
    # Defaults live exclusively in config/agent.toml; this class validates their types/units.
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    retrieval_top_k: int = Field(gt=0)
    retrieval_min_score: float = Field(ge=0)
    retrieval_candidate_min: int = Field(gt=0)
    retrieval_candidate_multiplier: int = Field(gt=0)
    retrieval_lexical_weight: float = Field(ge=0, le=1)
    retrieval_phrase_weight: float = Field(ge=0, le=1)
    retrieval_semantic_weight: float = Field(ge=0, le=1)
    retrieval_coverage_sharpness: float = Field(gt=0)
    retrieval_corpus_missing_ceiling: float = Field(ge=0, le=1)
    retrieval_corpus_min_chunks: int = Field(ge=0)
    retrieval_metadata_match_bonus: float = Field(ge=0)
    retrieval_metadata_conflict_penalty: float = Field(ge=0)
    retrieval_hash_trust_floor: float = Field(ge=0, le=1)
    retrieval_identity_min_score: float = Field(ge=0)
    identity_model_match_bonus: float = Field(ge=0)
    identity_platform_bonus: float = Field(ge=0)
    identity_model_conflict_penalty: float = Field(ge=0)
    identity_brand_match_bonus: float = Field(ge=0)
    identity_brand_conflict_penalty: float = Field(ge=0)
    identity_topic_bonus_cap: float = Field(ge=0)
    identity_topic_term_bonus: float = Field(ge=0)
    identity_address_bonus: float = Field(ge=0)
    identity_communication_bonus: float = Field(ge=0)
    identity_unrelated_control_penalty: float = Field(ge=0)
    identity_alarm_code_bonus: float = Field(ge=0)
    evidence_top_k: int = Field(gt=0)
    evidence_per_document: int = Field(gt=0)
    evidence_min_score: float = Field(ge=0)
    evidence_score_ratio: float = Field(ge=0, le=1)
    grounded_answer_min_score: float = Field(ge=0)
    evidence_total_chars: int = Field(gt=0)
    evidence_snippet_chars: int = Field(gt=0)
    chat_history_limit: int = Field(ge=0)
    history_message_chars: int = Field(gt=0)
    followup_message_limit: int = Field(gt=0)
    followup_user_chars: int = Field(gt=0)
    followup_assistant_chars: int = Field(gt=0)
    followup_query_chars: int = Field(gt=0)
    state_feedback_chars: int = Field(gt=0)
    state_feedback_limit: int = Field(gt=0)
    embedding_batch_size: int = Field(gt=0)
    embedding_max_length: int = Field(gt=0)
    agent_max_rounds: int = Field(gt=0)
    llm_temperature: float = Field(ge=0, le=2)
    local_llm_max_output_tokens: int = Field(gt=0)
    llm_max_output_tokens: int = Field(ge=0)
    conflict_score_ratio: float = Field(ge=0, le=1)
    conflict_max_groups: int = Field(gt=0)
    conflict_excerpts_per_group: int = Field(gt=0)
    conflict_excerpt_chars: int = Field(gt=0)
    citation_excerpt_chars: int = Field(gt=0)
    quiz_search_top_k: int = Field(gt=0)
    quiz_option_top_k: int = Field(gt=0)
    quiz_evidence_top_k: int = Field(gt=0)
    quiz_option_lead: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_relationships(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size（单位：字符）")
        weights = self.retrieval_lexical_weight + self.retrieval_phrase_weight
        if not math.isclose(weights + self.retrieval_semantic_weight, 1.0, abs_tol=1e-6):
            raise ValueError("检索 lexical/phrase/semantic 三项权重之和必须为 1")
        if self.evidence_snippet_chars > self.evidence_total_chars:
            raise ValueError("单片段字符预算不能大于资料总字符预算")
        return self


def resolve_tuning_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and not path.exists() and (ROOT / path).exists():
        path = ROOT / path
    return path.resolve()


def read_tuning(path: Path) -> dict:
    with path.open("rb") as stream:
        content = tomllib.load(stream)
    result = {}
    for section, values in content.items():
        if section not in SECTIONS or not isinstance(values, dict):
            raise ValueError(f"未知配置分组：{section}（{path.name}）")
        for key, value in values.items():
            if key not in TuningParameters.model_fields:
                raise ValueError(f"未知或不允许的调参项：{key}（{path.name}）")
            if key in result:
                raise ValueError(f"重复调参项：{key}（{path.name}）")
            result[key] = value
    return result


def tuning_snapshot(settings) -> dict:
    values = {name: getattr(settings, name) for name in TuningParameters.model_fields}
    identity = {
        "parameters": values,
        "embedding_backend": settings.embedding_backend,
        "embedding_dimension": settings.vector_dimension,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    local = Path.cwd() / "config/agent.toml"
    selected = (
        resolve_tuning_path(settings.agent_config_file)
        if settings.agent_config_file
        else (local.resolve() if local.exists() else BASE_CONFIG)
    )
    return {
        **identity,
        "fingerprint": fingerprint,
        "base_file": str(BASE_CONFIG),
        "config_file": str(selected),
        "experiment_file": str(resolve_tuning_path(settings.agent_experiment_file))
        if settings.agent_experiment_file
        else None,
    }
