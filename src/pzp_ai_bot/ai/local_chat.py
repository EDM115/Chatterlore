from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from typing import Any

from pzp_ai_bot.ai.local_runtime import (
    LocalInferenceWorker,
    import_local_dependency,
    local_loader_revision,
    resolve_device_dtype,
    validate_local_artifact_revision,
    validate_prepared_paths,
)
from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult
from pzp_ai_bot.local_models import LocalModelOptions


class LocalTransformersChatProvider:
    """Single-device causal LM generation without implicit model downloads."""

    name = "local"

    def __init__(self, model: str, *, options: LocalModelOptions | None = None) -> None:
        if not model.strip():
            raise ValueError("A local chat model must be configured")
        self.model = model
        self.options = options or LocalModelOptions()
        self._worker = LocalInferenceWorker(self.options)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._prepared_model_path: str | None = None
        self._prepared_tokenizer_path: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def busy(self) -> bool:
        return self._worker.busy

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        if reasoning_effort not in {None, "none"}:
            raise ValueError(
                "Embedded local chat does not support reasoning effort, omit that option"
            )
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("Local chat temperature must be finite and between zero and two")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("Local chat output token limit must be a positive integer")
        if not messages:
            raise ValueError("Local chat requires at least one message")
        if len(messages) > self.options.max_batch_inputs or any(
            message.role not in {"system", "user", "assistant"}
            or not isinstance(message.content, str)
            for message in messages
        ):
            raise ValueError("Local chat message count or format is invalid")
        if sum(len(message.content) for message in messages) > self.options.max_input_characters:
            raise ValueError("Local chat context exceeds the configured character limit")
        budget = min(max_tokens, self.options.max_new_tokens)
        payload = [{"role": message.role, "content": message.content} for message in messages]
        return await self._worker.run(
            lambda stop: self._generate_sync(payload, temperature, budget, stop)
        )

    def _load_sync(
        self,
        *,
        local_files_only: bool,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
    ) -> None:
        if self._model is not None:
            if (model_path is not None and model_path != self._prepared_model_path) or (
                tokenizer_path is not None and tokenizer_path != self._prepared_tokenizer_path
            ):
                raise ValueError(
                    "Retire the loaded local model before preparing a different snapshot"
                )
            return
        model_reference = model_path or self.model
        tokenizer_reference = tokenizer_path or model_reference
        validate_local_artifact_revision(model_reference, self.options.revision)
        if tokenizer_reference != model_reference:
            validate_local_artifact_revision(
                tokenizer_reference, self.options.tokenizer_revision or self.options.revision
            )
        torch = import_local_dependency("torch")
        transformers = import_local_dependency("transformers")
        device, dtype = resolve_device_dtype(torch, self.options)
        controls = {
            "cache_dir": self.options.cache_dir,
            "local_files_only": True,
            "trust_remote_code": False,
        }
        try:
            tokenizer = transformers.AutoTokenizer.from_pretrained(
                tokenizer_reference,
                revision=local_loader_revision(
                    tokenizer_reference, self.options.tokenizer_revision or self.options.revision
                ),
                **controls,
            )
        except Exception:
            raise RuntimeError(
                "Cannot load local tokenizer safely, explicitly prepare the permitted model cache"
            ) from None
        try:
            tokenizer.get_chat_template()
        except Exception:
            raise ValueError(
                "No usable chat template, configure a chat-trained tokenizer with its own template"
            ) from None
        try:
            model = transformers.AutoModelForCausalLM.from_pretrained(
                model_reference,
                revision=local_loader_revision(model_reference, self.options.revision),
                dtype=dtype,
                use_safetensors=True,
                **controls,
            )
            model.to(device)
            model.eval()
        except Exception:
            raise RuntimeError(
                "Cannot load local chat, check device/dtype/safe weights and explicitly prepare"
            ) from None
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._prepared_model_path = model_path
        self._prepared_tokenizer_path = tokenizer_path

    def _generate_sync(
        self, messages: list[dict[str, str]], temperature: float, budget: int, stop: threading.Event
    ) -> ChatResult:
        self._load_sync(local_files_only=True)
        if stop.is_set():
            raise RuntimeError("Local chat inference was cancelled before generation")
        try:
            inputs = self._tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
                truncation=False,
            )
        except Exception:
            raise ValueError(
                "The model chat template rejects these messages, choose a compatible chat model"
            ) from None
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        if prompt_tokens < 1 or prompt_tokens > self.options.max_input_tokens:
            raise ValueError(
                "Local chat context exceeds its input token limit, reduce the request explicitly"
            )
        limits = [self.options.max_input_tokens + self.options.max_new_tokens]
        for value in (
            getattr(self._model.config, "max_position_embeddings", None),
            getattr(self._tokenizer, "model_max_length", None),
        ):
            if isinstance(value, int) and 0 < value < 1000000000:
                limits.append(value)
        if prompt_tokens + budget > min(limits):
            raise ValueError(
                "Input plus output exceeds the model context window, reduce either limit"
            )

        transformers = import_local_dependency("transformers")
        torch = self._torch

        class CancelWhenRequested(transformers.StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return torch.full(
                    (input_ids.shape[0],), stop.is_set(), device=input_ids.device, dtype=torch.bool
                )

        parameters: dict[str, Any] = {
            "max_new_tokens": budget,
            "do_sample": temperature > 0,
            "stopping_criteria": transformers.StoppingCriteriaList([CancelWhenRequested()]),
            "return_dict_in_generate": False,
            "num_beams": 1,
            "num_return_sequences": 1,
            "output_scores": False,
            "output_logits": False,
            "output_attentions": False,
            "output_hidden_states": False,
        }
        if temperature > 0:
            parameters.update(temperature=temperature, top_p=self.options.top_p)
        pad_id = self._tokenizer.pad_token_id
        if pad_id is None:
            pad_id = self._tokenizer.eos_token_id
        if pad_id is not None:
            parameters["pad_token_id"] = pad_id
        try:
            inputs = {key: value.to(self._model.device) for key, value in inputs.items()}
            with torch.inference_mode():
                output = self._model.generate(**inputs, **parameters)
            generated = output[0, prompt_tokens:]
            content = self._tokenizer.decode(generated, skip_special_tokens=True)
        except Exception:
            raise RuntimeError(
                "Local chat generation failed, check the model, device and context limits"
            ) from None
        return ChatResult(
            content=content,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=int(generated.shape[-1]),
        )

    async def prepare(
        self,
        *,
        allow_download: bool = False,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
    ) -> None:
        def prepare_sync(stop: threading.Event) -> None:
            prepared_model, prepared_tokenizer = validate_prepared_paths(
                allow_download=allow_download, model_path=model_path, tokenizer_path=tokenizer_path
            )
            self._load_sync(
                local_files_only=True, model_path=prepared_model, tokenizer_path=prepared_tokenizer
            )

        await self._worker.run(prepare_sync)

    async def close(self) -> None:
        def release() -> None:
            self._model = None
            self._tokenizer = None
            self._torch = None

        await self._worker.close(release)

    async def aclose(self) -> None:
        await self.close()
