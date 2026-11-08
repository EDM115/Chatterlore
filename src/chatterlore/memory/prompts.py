from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "2026-06-23"
SUMMARY_PROMPT_VERSION = PROMPT_VERSION
ANSWER_PROMPT_VERSION = PROMPT_VERSION
CANONICAL_PROMPT_VERSION = PROMPT_VERSION


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    version: str
    prompt: str


BUILTIN_SYSTEM_PROMPTS: dict[str, PromptTemplate] = {
    "scoped": PromptTemplate(
        name="scoped",
        version=ANSWER_PROMPT_VERSION,
        prompt=(
            "Answer from scoped memory only. If the context is insufficient, say so. "
            "Include compact source references. Match the user's language unless the memory "
            "itself requires another language."
        ),
    ),
    "strict": PromptTemplate(
        name="strict",
        version=ANSWER_PROMPT_VERSION,
        prompt=(
            "Answer only when the provided scoped memory directly supports the answer. "
            "Do not infer from outside knowledge. Cite compact source references. Match the "
            "user's language unless the memory itself requires another language."
        ),
    ),
    "brief": PromptTemplate(
        name="brief",
        version=ANSWER_PROMPT_VERSION,
        prompt=(
            "Answer briefly from scoped memory only. Say when context is insufficient "
            "and cite compact source references. Match the user's language unless the memory "
            "itself requires another language."
        ),
    ),
}

SUMMARY_BASE_PROMPT = PromptTemplate(
    name="summary_base",
    version=SUMMARY_PROMPT_VERSION,
    prompt=(
        "Summarize durable group-chat memory. Preserve the source language for facts and names, "
        "and do not translate unless the template asks for it."
    ),
)

ANSWER_INSUFFICIENT_CONTEXT = "I do not have enough scoped memory to answer that confidently yet."


def prompt_metadata(
    *, template_name: str, template_version: str, reasoning_level: str, provider_profile: str
) -> dict[str, str]:
    return {
        "prompt_version": PROMPT_VERSION,
        "system_prompt_template": template_name,
        "system_prompt_version": template_version,
        "reasoning_level": reasoning_level,
        "provider_profile": provider_profile,
    }
