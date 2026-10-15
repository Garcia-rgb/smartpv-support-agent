"""文本向量后端：把「文本 → 向量」从检索逻辑里拆出来，做成可切换的实现。

为什么需要这一层：原来的向量是本地哈希碰撞（`embeddings.local_embedding`），
它只保证流程跑得通，不理解语义。实测两个话题完全不同的句子，只要常用字撞上
就会给出不低的相似度——「Python 怎么装环境」命中的段落是「2.7 设备安装要点」，
分数比一些真问题还高。这个毛病靠调权重治不好，只有换成真正的语义模型才行。

但模型文件不该进仓库（90MB 的二进制），CI 也不该依赖它。所以默认仍是零依赖的
哈希向量，配好模型目录再切到 ONNX 语义向量；两者维度不同（384 / 512），
**换后端必须重建表并重新导入语料**，否则库里的旧向量和新查询向量不在一个空间。
"""

import abc
import asyncio
from collections.abc import Sequence
from pathlib import Path

from .embeddings import local_embedding

# 各后端的默认维度。留在这里而不是散落在调用方，是为了让「换后端」只有一处真相。
BACKEND_DIMENSIONS = {"hash": 384, "onnx": 512}

# ONNX 模型目录的默认约定：目录下放 tokenizer.json，权重放 onnx/model.onnx
# （Xenova 系仓库的摆放方式）。也接受直接把 model.onnx 放在目录根下。
ONNX_WEIGHTS_RELATIVE = Path("onnx") / "model.onnx"
ONNX_WEIGHTS_FLAT = Path("model.onnx")
TOKENIZER_FILE = Path("tokenizer.json")


class EmbeddingUnavailableError(RuntimeError):
    """配置了语义向量后端，但依赖或模型文件不可用。

    这里刻意**不**静默退回哈希向量：哈希向量和语义向量不是「差一点」，
    而是两个不同的空间，混用会让检索结果变得无法解释。配置写了 onnx
    就应该按 onnx 跑，跑不了要报出来让人修。
    """


class EmbeddingBackend(abc.ABC):
    """向量后端协议：给一批文本，返回一批等长、已归一化的向量。"""

    name: str = "base"

    # 语义分要不要按词面覆盖率打折，由后端自己声明。
    #
    # 这个折扣是给哈希向量设的：字符级碰撞下，覆盖率越低，那块「相似」越可能是
    # 常用字撞出来的，所以要按覆盖率把语义分压下去。真语义模型没有这个毛病，
    # 折扣就变成了纯粹的压制——实测 30 条库内问题，去掉折扣后 top-1 命中从
    # 25/30 到 26/30；折扣开着时，换语义向量一条都没多。差别只有一条，
    # 样本量不足以支撑调权重，但方向明确：不给真模型打折。
    semantic_trust_floor: float = 1.0

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """向量维度。调用方靠它判断库里的旧向量还能不能用。"""

    @property
    def signature(self) -> str:
        """后端指纹，写进片段的元数据，用来发现「库是 A 模型建的、查询用的是 B 模型」。"""
        return f"{self.name}:{self.dimension}"

    @abc.abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """同步生成向量。ONNX 推理是 CPU 密集的，异步调用请走 `embed_async`。"""

    async def embed_async(self, texts: Sequence[str]) -> list[list[float]]:
        """在线程里跑推理，避免把 FastAPI 的事件循环堵住。

        一次查询就要跑一遍模型，几十毫秒的同步推理足以让同一进程里的其他请求
        排队等待；丢到线程里则互不干扰。
        """
        if not texts:
            return []
        return await asyncio.to_thread(self.embed, texts)


class HashEmbeddingBackend(EmbeddingBackend):
    """零依赖兜底：哈希碰撞向量。只保证流程可跑通，不具备语义能力。"""

    def __init__(self, dimension: int = BACKEND_DIMENSIONS["hash"]):
        self.name = "hash"
        self._dimension = dimension

    # 哈希向量必须打折，理由见基类上的说明。
    semantic_trust_floor = 0.4

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [local_embedding(text, self._dimension) for text in texts]


class OnnxEmbeddingBackend(EmbeddingBackend):
    """本地 ONNX 语义模型（默认 bge-small-zh-v1.5，512 维）。

    选它是因为能在本机离线跑、无 API 成本、CI 可复现。中文检索场景下
    small 档的模型已经够用：参数量小、CPU 上单条几十毫秒。

    池化用 [CLS] 位而不是平均池化。BGE 系列的官方用法就是取 [CLS]，
    实测也确实分得更开：相关句子 +0.543、无关最高 +0.361（平均池化是
    +0.508 / +0.289），间隔从 0.219 拉到 0.182 以上。
    """

    def __init__(
        self,
        model_dir: Path,
        dimension: int,
        *,
        batch_size: int | None = None,
        max_length: int | None = None,
    ):
        if batch_size is None or max_length is None:
            from ..config import get_settings

            settings = get_settings()
            batch_size = settings.embedding_batch_size if batch_size is None else batch_size
            max_length = settings.embedding_max_length if max_length is None else max_length
        # 先查文件再导入依赖：路径写错是最常见的配置问题，不该因为没装 onnxruntime
        # 而报出一个不相干的错，也不该为一次配置检查付出几十兆的导入代价。
        weights = model_dir / ONNX_WEIGHTS_RELATIVE
        if not weights.exists():
            weights = model_dir / ONNX_WEIGHTS_FLAT
        tokenizer_path = model_dir / TOKENIZER_FILE
        if not weights.exists() or not tokenizer_path.exists():
            raise EmbeddingUnavailableError(
                f"模型目录不完整：{model_dir}。需要 {TOKENIZER_FILE} 和 "
                f"{ONNX_WEIGHTS_RELATIVE}（或 {ONNX_WEIGHTS_FLAT}）"
            )

        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise EmbeddingUnavailableError(
                "使用 ONNX 语义向量需要额外依赖，请先安装：pip install onnxruntime tokenizers"
            ) from exc

        self._np = np
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=max_length)
        # 同批文本长度不一，必须补齐；否则 onnxruntime 无法拼成矩形张量。
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self._tokenizer = tokenizer
        self._session = ort.InferenceSession(str(weights), providers=["CPUExecutionProvider"])
        self._input_names = {item.name for item in self._session.get_inputs()}
        self._batch_size = max(batch_size, 1)
        self.name = "onnx"
        self._dimension = dimension
        # 模型实际输出维度优先于配置：配置写错时早失败，好过悄悄存下一堆错长度的向量。
        actual = self._session.get_outputs()[0].shape[-1]
        if isinstance(actual, int) and actual != dimension:
            raise EmbeddingUnavailableError(
                f"模型输出维度是 {actual}，但配置的 EMBEDDING_DIMENSION 是 {dimension}；"
                "两处必须一致，改完还要重建表并重新导入语料"
            )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed_batch(list(texts[start : start + self._batch_size])))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        np = self._np
        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([item.ids for item in encoded], dtype=np.int64)
        attention_mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self._session.run(None, feeds)[0]
        cls = hidden[:, 0]
        # 归一化之后，点积就等于余弦相似度，和哈希向量保持同一套比较方式。
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        return (cls / np.clip(norms, 1e-9, None)).tolist()


_BACKENDS: dict[str, EmbeddingBackend] = {}


def build_embedding_backend(settings) -> EmbeddingBackend:
    """按配置构造后端。不认识的取值直接报错，不做猜测。"""
    name = (settings.embedding_backend or "hash").strip().lower()
    if name == "hash":
        return HashEmbeddingBackend(settings.vector_dimension)
    if name == "onnx":
        if not settings.embedding_model_path:
            raise EmbeddingUnavailableError(
                "EMBEDDING_BACKEND=onnx 时必须指定 EMBEDDING_MODEL_PATH（模型目录）"
            )
        return OnnxEmbeddingBackend(
            Path(settings.embedding_model_path),
            settings.vector_dimension,
            batch_size=settings.embedding_batch_size,
            max_length=settings.embedding_max_length,
        )
    raise EmbeddingUnavailableError(f"未知的 embedding 后端：{settings.embedding_backend}")


def get_embedding_backend(settings=None) -> EmbeddingBackend:
    """按配置取后端实例，同一种配置只加载一次。

    模型加载要几百毫秒到几秒，不能每次请求都来一遍；但缓存键必须带上配置，
    否则测试里换成哈希后端时会拿到之前加载好的 ONNX 实例。
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    key = "|".join(
        [
            str(settings.embedding_backend),
            str(settings.embedding_model_path),
            str(settings.vector_dimension),
            str(settings.embedding_batch_size),
            str(settings.embedding_max_length),
        ]
    )
    if key not in _BACKENDS:
        _BACKENDS[key] = build_embedding_backend(settings)
    return _BACKENDS[key]


def reset_embedding_backends() -> None:
    """清空后端缓存，供测试在更换配置后重新加载。"""
    _BACKENDS.clear()
