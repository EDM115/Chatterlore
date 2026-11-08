from __future__ import annotations

import asyncio
import importlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chatterlore.ai.protocols import ChatMessage


def test_local_options_are_frozen_and_reject_unsafe_or_unbounded_values() -> None:
    from chatterlore.local_models import LocalModelOptions

    options = LocalModelOptions()
    assert options.local_files_only is True
    assert options.prompt_mode == "generic"
    for name, value in {"device": "cuda:1", "revision": "new-revision"}.items():
        with pytest.raises(ValidationError):
            setattr(options, name, value)
    for values in (
        {"device": "mps"},
        {"device": "cuda:-1"},
        {"dtype": "int8"},
        {"batch_size": 0},
        {"max_input_tokens": 0},
        {"max_new_tokens": -1},
        {"top_p": float("nan")},
        {"inference_timeout_seconds": float("inf")},
        {"revision": " "},
        {"truncate_dim": 0},
        {"trust_remote_code": True},
    ):
        with pytest.raises(ValidationError):
            LocalModelOptions.model_validate(values)


@pytest.fixture
def tiny_chat_model(tmp_path: Path) -> Path:
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    tokenizers = pytest.importorskip("tokenizers")
    path = tmp_path / "tiny-chat"
    vocab = {
        "answer": 0,
        "[PAD]": 1,
        "[EOS]": 2,
        "[UNK]": 3,
        "user": 4,
        "assistant": 5,
        "private": 6,
        "system": 7,
    }
    special_tokens = {"pad_token": "[PAD]", "eos_token": "[EOS]", "unk_token": "[UNK]"}
    backend = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(vocab, unk_token=special_tokens["unk_token"])
    )
    backend.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(tokenizer_object=backend, **special_tokens)
    tokenizer.chat_template = "".join(
        (
            "{% for message in messages %}",
            "{{ message['role'] }} {{ message['content'] }} ",
            "{% endfor %}",
            "{% if add_generation_prompt %}assistant{% endif %}",
        )
    )
    tokenizer.save_pretrained(path)
    config = transformers.GPT2Config(
        vocab_size=8,
        n_positions=32,
        n_embd=8,
        n_layer=1,
        n_head=1,
        bos_token_id=None,
        eos_token_id=2,
        pad_token_id=1,
    )
    model = transformers.GPT2LMHeadModel(config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
    model.save_pretrained(path, safe_serialization=True)
    return path


@pytest.fixture
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    original_connect = socket.socket.connect

    def forbidden(connection, address):
        if address[0] in {"127.0.0.1", "::1"}:
            return original_connect(connection, address)
        raise AssertionError("Local inference attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


async def test_real_hf_generation_uses_template_decodes_only_new_tokens_and_tracks_usage(
    tiny_chat_model: Path, forbid_network
) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.local_models import LocalModelOptions

    provider = LocalTransformersChatProvider(
        str(tiny_chat_model),
        options=LocalModelOptions(
            device="cpu", dtype="float32", max_input_tokens=16, max_new_tokens=3
        ),
    )
    assert provider.loaded is False
    result = await provider.generate(
        [ChatMessage(role="user", content="private")], temperature=0, max_tokens=10
    )
    assert result.content == "answer answer answer"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 3
    assert provider.loaded is True
    assert provider.busy is False
    await provider.aclose()
    assert provider.loaded is False
    with pytest.raises(RuntimeError, match="closed"):
        await provider.generate([ChatMessage(role="user", content="private")])


async def test_real_hf_generation_rejects_oversized_context_without_truncation(
    tiny_chat_model: Path,
) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.local_models import LocalModelOptions

    provider = LocalTransformersChatProvider(
        str(tiny_chat_model), options=LocalModelOptions(device="cpu", max_input_tokens=4)
    )
    with pytest.raises(ValueError, match="context"):
        await provider.generate(
            [ChatMessage(role="user", content="private private private private")], max_tokens=1
        )
    await provider.close()


async def test_real_hf_generation_rejects_missing_chat_template(tiny_chat_model: Path) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider

    transformers = pytest.importorskip("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(tiny_chat_model, local_files_only=True)
    tokenizer.chat_template = None
    tokenizer.save_pretrained(tiny_chat_model)
    template_path = tiny_chat_model / "chat_template.jinja"
    template_path.unlink(missing_ok=True)
    provider = LocalTransformersChatProvider(str(tiny_chat_model))
    with pytest.raises(ValueError, match="chat template"):
        await provider.generate([ChatMessage(role="user", content="private")])
    await provider.close()


async def test_local_chat_does_not_claim_reasoning_support(tiny_chat_model: Path) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider

    provider = LocalTransformersChatProvider(str(tiny_chat_model))
    with pytest.raises(ValueError, match="reasoning"):
        await provider.generate(
            [ChatMessage(role="user", content="private")], reasoning_effort="high"
        )
    assert provider.loaded is False


def install_embedding_double(monkeypatch: pytest.MonkeyPatch, encoder, before_load=None):
    real_import = importlib.import_module
    loads: list[dict] = []

    def construct(model, **kwargs):
        loads.append({"model": model, **kwargs})
        if before_load is not None:
            before_load()
        return encoder

    def controlled_import(name):
        if name == "sentence_transformers":
            return SimpleNamespace(SentenceTransformer=construct)
        if name == "torch":
            return SimpleNamespace(
                cuda=SimpleNamespace(is_available=lambda: False), float32="float32"
            )
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", controlled_import)
    return loads


class PurposeEncoder:
    max_seq_length = 512

    def get_embedding_dimension(self):
        return 2

    def encode(self, texts, **kwargs):
        return [[1.0, 0.0] for _ in texts]

    def encode_query(self, texts, **kwargs):
        return [[0.0, 1.0] for _ in texts]

    def encode_document(self, texts, **kwargs):
        return [[-1.0, 0.0] for _ in texts]


async def test_embedding_purpose_and_generic_compatibility_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    loads = install_embedding_double(monkeypatch, PurposeEncoder())
    provider = LocalSentenceTransformerEmbeddingProvider(
        "fixture", options=LocalModelOptions(prompt_mode="query_document")
    )
    assert await provider.embed(["hello"]) == [[1.0, 0.0]]
    assert await provider.embed_query(["hello"]) == [[0.0, 1.0]]
    assert await provider.embed_documents(["hello"]) == [[-1.0, 0.0]]
    assert provider.dimensions == 2
    assert len(loads) == 1
    assert loads[0]["local_files_only"] is True
    assert loads[0]["trust_remote_code"] is False
    assert loads[0]["model_kwargs"]["use_safetensors"] is True
    await provider.close()


async def test_generic_embedding_mode_keeps_legacy_query_document_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider

    install_embedding_double(monkeypatch, PurposeEncoder())
    provider = LocalSentenceTransformerEmbeddingProvider("fixture")
    assert await provider.embed_query(["hello"]) == [[1.0, 0.0]]
    assert await provider.embed_documents(["hello"]) == [[1.0, 0.0]]
    await provider.close()


@pytest.mark.parametrize(
    "vectors",
    [[[float("nan"), 1.0]], [[1.0]], [], [[1.0, 2.0], [3.0, 4.0]], [[True, 0.0]], [["1.0", 0.0]]],
)
async def test_malformed_embeddings_fail_safely(monkeypatch: pytest.MonkeyPatch, vectors) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider

    encoder = PurposeEncoder()
    encoder.encode = lambda *args, **kwargs: vectors
    install_embedding_double(monkeypatch, encoder)
    provider = LocalSentenceTransformerEmbeddingProvider("fixture")
    with pytest.raises(RuntimeError, match="embedding"):
        await provider.embed(["private"])
    await provider.close()


async def test_embedding_inference_never_downloads_without_explicit_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    loads = install_embedding_double(monkeypatch, PurposeEncoder())
    options = LocalModelOptions(local_files_only=False)
    provider = LocalSentenceTransformerEmbeddingProvider("fixture", options=options)
    await provider.embed(["hello"])
    assert loads[0]["local_files_only"] is True
    await provider.close()
    provider = LocalSentenceTransformerEmbeddingProvider("fixture", options=options)
    await provider.prepare()
    assert loads[1]["local_files_only"] is True
    await provider.close()
    provider = LocalSentenceTransformerEmbeddingProvider("fixture", options=options)
    with pytest.raises(ValueError, match="bounded preparation"):
        await provider.prepare(allow_download=True)
    assert len(loads) == 2
    await provider.close()


async def test_chat_prepares_verified_snapshots_without_changing_semantic_model(
    tiny_chat_model: Path, forbid_network
) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.local_models import LocalModelOptions

    provider = LocalTransformersChatProvider(
        "not-cached/configured-model",
        options=LocalModelOptions(device="cpu", max_new_tokens=2, local_files_only=False),
    )
    await provider.prepare(model_path=str(tiny_chat_model), tokenizer_path=str(tiny_chat_model))
    result = await provider.generate([ChatMessage(role="user", content="private")], temperature=0)
    assert result.content == "answer answer"
    assert result.model == "not-cached/configured-model"
    with pytest.raises(ValueError, match="bounded preparation"):
        await provider.prepare(allow_download=True)
    await provider.close()


async def test_embedding_prepares_verified_snapshot_and_does_not_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.ai.local_runtime import local_directory_revision

    loads = install_embedding_double(monkeypatch, PurposeEncoder())
    from chatterlore.local_models import LocalModelOptions

    (tmp_path / "model.safetensors").write_bytes(b"controlled fixture weights")
    provider = LocalSentenceTransformerEmbeddingProvider(
        "configured-model",
        options=LocalModelOptions(revision=local_directory_revision(str(tmp_path))),
    )
    await provider.prepare(model_path=str(tmp_path))
    assert loads[0]["model"] == str(tmp_path)
    assert loads[0]["revision"] is None
    assert loads[0]["local_files_only"] is True
    assert await provider.embed(["hello"]) == [[1.0, 0.0]]
    assert provider.embedding_model == "configured-model"
    await provider.close()


async def test_prepared_path_validation_runs_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import chatterlore.ai.local as local

    loop_thread = threading.get_ident()
    original = local.validate_prepared_paths

    def check_worker(**kwargs):
        assert threading.get_ident() != loop_thread
        return original(**kwargs)

    install_embedding_double(monkeypatch, PurposeEncoder())
    monkeypatch.setattr(local, "validate_prepared_paths", check_worker)
    provider = local.LocalSentenceTransformerEmbeddingProvider("configured-model")
    await provider.prepare(model_path=str(tmp_path))
    await provider.close()


async def test_prepared_chat_paths_do_not_receive_semantic_directory_revision(
    tiny_chat_model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.ai.local_runtime import local_directory_revision
    from chatterlore.local_models import LocalModelOptions

    transformers = pytest.importorskip("transformers")
    original = transformers.AutoModelForCausalLM.from_pretrained
    seen = []

    def load(*args, **kwargs):
        seen.append(kwargs.get("revision"))
        return original(*args, **kwargs)

    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained", load)
    provider = LocalTransformersChatProvider(
        "configured-model",
        options=LocalModelOptions(
            device="cpu", revision=local_directory_revision(str(tiny_chat_model))
        ),
    )
    try:
        await provider.prepare(model_path=str(tiny_chat_model))
        assert seen == [None]
    finally:
        await provider.close()


async def test_chat_cancellation_cooperatively_stops_real_generation(tiny_chat_model: Path) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.local_models import LocalModelOptions

    provider = LocalTransformersChatProvider(
        str(tiny_chat_model),
        options=LocalModelOptions(device="cpu", max_new_tokens=16, max_queue_size=0),
    )
    await provider.prepare()
    started = threading.Event()
    release = threading.Event()
    forward_calls = 0

    def pause_forward(module, args):
        nonlocal forward_calls
        forward_calls += 1
        started.set()
        assert release.wait(5)

    hook = provider._model.register_forward_pre_hook(pause_forward)
    first = asyncio.create_task(
        provider.generate(
            [ChatMessage(role="user", content="private")], temperature=0, max_tokens=16
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert provider.busy is True
        with pytest.raises(RuntimeError, match="queue"):
            await provider.generate(
                [ChatMessage(role="user", content="private")], temperature=0, max_tokens=1
            )
    finally:
        release.set()
        await provider.close()
        hook.remove()
    assert forward_calls == 1


async def test_chat_overrides_saved_multi_sequence_generation_settings(
    tiny_chat_model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatterlore.ai.local import LocalTransformersChatProvider
    from chatterlore.local_models import LocalModelOptions

    provider = LocalTransformersChatProvider(
        str(tiny_chat_model), options=LocalModelOptions(device="cpu", max_new_tokens=2)
    )
    await provider.prepare()
    model = provider._model
    model.generation_config.num_beams = 4
    model.generation_config.num_return_sequences = 4
    original = model.generate
    observed = {}

    def generate(*args, **kwargs):
        observed["beams"] = kwargs.get("num_beams", model.generation_config.num_beams)
        observed["sequences"] = kwargs.get(
            "num_return_sequences", model.generation_config.num_return_sequences
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(model, "generate", generate)
    try:
        result = await provider.generate(
            [ChatMessage(role="user", content="private")], temperature=0
        )
        assert result.completion_tokens == 2
        assert observed == {"beams": 1, "sequences": 1}
    finally:
        await provider.close()


async def test_simultaneous_first_embedding_calls_load_once_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider

    started = threading.Event()
    release = threading.Event()

    def pause_load():
        started.set()
        assert release.wait(5)

    loads = install_embedding_double(monkeypatch, PurposeEncoder(), pause_load)
    provider = LocalSentenceTransformerEmbeddingProvider("fixture")
    first = asyncio.create_task(provider.embed(["first"]))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(provider.embed(["second"]))
        await asyncio.sleep(0)
        assert not second.done()
        assert provider.busy is True
        release.set()
        assert await asyncio.gather(first, second) == [[[1.0, 0.0]], [[1.0, 0.0]]]
        assert len(loads) == 1
    finally:
        release.set()
        await provider.close()


async def test_cancelled_embedding_keeps_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    started = threading.Event()
    release = threading.Event()
    active = 0
    maximum = 0

    class BlockingEncoder(PurposeEncoder):
        def encode(self, texts, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            started.set()
            assert release.wait(5)
            active -= 1
            return [[1.0, 0.0] for _ in texts]

    loads = install_embedding_double(monkeypatch, BlockingEncoder())
    provider = LocalSentenceTransformerEmbeddingProvider(
        "fixture", options=LocalModelOptions(queue_timeout_seconds=0.05, max_queue_size=1)
    )
    first = asyncio.create_task(provider.embed(["hello"]))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(TimeoutError, match="queue"):
            await provider.embed(["second"])
        assert provider.busy is True
        assert maximum == 1
        assert len(loads) == 1
    finally:
        release.set()
        await provider.close()


async def test_real_sentence_transformer_roundtrip_is_offline_and_normalized(
    tmp_path: Path, forbid_network
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    sentence_transformers = pytest.importorskip("sentence_transformers")
    modules = pytest.importorskip("sentence_transformers.sentence_transformer.modules")
    path = tmp_path / "tiny-embeddings"
    model = sentence_transformers.SentenceTransformer(
        modules=[modules.BoW(["hello", "world", "other"])], device="cpu"
    )
    model.save(str(path))
    provider = LocalSentenceTransformerEmbeddingProvider(
        str(path), options=LocalModelOptions(device="cpu")
    )
    vectors = await provider.embed(["hello world", "other"])
    assert vectors[0] == pytest.approx([0.70710678, 0.70710678, 0.0])
    assert vectors[1] == [0.0, 0.0, 1.0]
    assert provider.dimensions == 3
    await provider.aclose()
    assert provider.loaded is False


async def test_real_transformer_embedding_loader_uses_safe_local_weights(
    tiny_chat_model: Path, forbid_network
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    pytest.importorskip("sentence_transformers")
    provider = LocalSentenceTransformerEmbeddingProvider(
        str(tiny_chat_model), options=LocalModelOptions(device="cpu")
    )
    try:
        assert await provider.embed(["private"]) == [[0.0] * 8]
        assert provider.dimensions == 8
    finally:
        await provider.close()


async def test_embedding_timeout_does_not_allow_overlapping_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatterlore.ai.local import LocalSentenceTransformerEmbeddingProvider
    from chatterlore.local_models import LocalModelOptions

    release = threading.Event()

    class BlockingEncoder(PurposeEncoder):
        def encode(self, texts, **kwargs):
            assert release.wait(5)
            return [[1.0, 0.0] for _ in texts]

    install_embedding_double(monkeypatch, BlockingEncoder())
    provider = LocalSentenceTransformerEmbeddingProvider(
        "fixture", options=LocalModelOptions(inference_timeout_seconds=0.05, max_queue_size=0)
    )
    try:
        with pytest.raises(TimeoutError, match="inference"):
            await provider.embed(["first"])
        with pytest.raises(RuntimeError, match="queue"):
            await provider.embed(["second"])
    finally:
        release.set()
        await provider.close()
