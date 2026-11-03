"""Corrected Opus pricing, adaptive-thinking
gating per model family, low-cache-prompt observability, and the
harness side of the MAVERICK_GOLD_PATCH plumbing.
"""
from __future__ import annotations

import logging

from maverick.budget import Budget
from maverick.llm import MODEL_OPUS


class TestOpusPricingHotfix:
    """Opus 4.7 is $5/$25 per Mtok — NOT $15/$75 (those were legacy
    Opus 4.0/4.1 rates). An earlier commit incorrectly raised
    Lightwork to $15/$75; this restores the correct value.
    Cross-check: vals.ai measures Opus 4.7 at $2.42/test on SWE-bench
    Verified, which reconciles ONLY with $5/$25 pricing.
    """

    def test_opus_input_priced_at_5_per_mtok(self):
        b = Budget(max_dollars=100.0, max_input_tokens=10_000_000)
        b.record_tokens(1_000_000, 0, model=MODEL_OPUS)
        assert abs(b.dollars - 5.0) < 0.001

    def test_opus_output_priced_at_25_per_mtok(self):
        b = Budget(max_dollars=100.0, max_input_tokens=10_000_000, max_output_tokens=10_000_000)
        b.record_tokens(0, 1_000_000, model=MODEL_OPUS)
        assert abs(b.dollars - 25.0) < 0.001

    def test_opus_cache_read_at_one_tenth_5(self):
        b = Budget(max_dollars=100.0, max_input_tokens=10_000_000)
        b.record_tokens(0, 0, model=MODEL_OPUS, cache_read_tok=1_000_000)
        # $5 input × 0.1 = $0.50
        assert abs(b.dollars - 0.5) < 0.001


class TestOpus47AdaptiveThinking:
    """Opus 4.7 rejects manual `thinking={"type":"enabled"}` with 400.
    Must use `adaptive`. Verified at platform.claude.com/docs/.../adaptive-thinking."""

    def test_opus_47_with_budget_uses_adaptive(self):
        from maverick.providers.anthropic_provider import AnthropicClient
        client = AnthropicClient.__new__(AnthropicClient)
        kwargs = client._build_request(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=4096,
            thinking_budget=8000,
            model="claude-opus-4-7",
        )
        assert kwargs.get("thinking", {}).get("type") == "adaptive"
        # No budget_tokens — adaptive auto-sizes.
        assert "budget_tokens" not in kwargs.get("thinking", {})

    def test_opus_47_without_budget_still_adaptive(self):
        from maverick.providers.anthropic_provider import AnthropicClient
        client = AnthropicClient.__new__(AnthropicClient)
        kwargs = client._build_request(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=4096,
            thinking_budget=None,
            model="claude-opus-4-7",
        )
        assert kwargs.get("thinking", {}).get("type") == "adaptive"

    def test_sonnet_46_uses_enabled_mode(self):
        """Sonnet 4.6 supports both 'enabled' and 'adaptive'; we use
        explicit 'enabled' with budget_tokens for callsites that pass
        thinking_budget."""
        from maverick.providers.anthropic_provider import AnthropicClient
        client = AnthropicClient.__new__(AnthropicClient)
        kwargs = client._build_request(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=4096,
            thinking_budget=8000,
            model="claude-sonnet-4-6",
        )
        thinking = kwargs.get("thinking", {})
        assert thinking.get("type") == "enabled"
        assert thinking.get("budget_tokens") == 8000


class TestLowCacheWarning:
    """The min cacheable block on Opus 4.6/4.5 and Haiku 4.5 is 4096 tokens
    (2048 for Opus 4.7; 1024 for everything else, including the current
    defaults Sonnet 4.6 and Opus 4.8), so a small enough system+tools block
    won't actually cache. Operators need to see this in logs.

    The table used to put Sonnet 4.6/4.5 and Opus 4.7/4.8 at the 4096
    minimum too (verified wrong against
    https://platform.claude.com/docs/en/build-with-claude/prompt-caching:
    Opus 4.7 is 2048, the rest are 1024)."""

    def test_min_cache_tokens_for_modern_models(self):
        from maverick.providers.anthropic_provider import _min_cache_tokens
        assert _min_cache_tokens("claude-opus-4-7") == 2048
        assert _min_cache_tokens("claude-opus-4-6") == 4096
        assert _min_cache_tokens("claude-opus-4-5") == 4096
        assert _min_cache_tokens("claude-opus-4-8") == 1024
        assert _min_cache_tokens("claude-sonnet-4-6") == 1024
        assert _min_cache_tokens("claude-sonnet-4-5") == 1024
        assert _min_cache_tokens("claude-haiku-4-5") == 4096

    def test_min_cache_tokens_for_current_generation_models(self):
        from maverick.providers.anthropic_provider import _min_cache_tokens
        assert _min_cache_tokens("claude-opus-5") == 512
        assert _min_cache_tokens("claude-sonnet-5") == 1024
        assert _min_cache_tokens("claude-fable-5-1") == 512

    def test_min_cache_tokens_for_legacy_models(self):
        from maverick.providers.anthropic_provider import _min_cache_tokens
        assert _min_cache_tokens("claude-3-5-sonnet-20241022") == 1024
        assert _min_cache_tokens("claude-opus-4-1") == 1024

    def test_warning_emitted_for_small_prompt(self, caplog):
        from maverick.providers import anthropic_provider as ap
        # Reset the once-per-process flag for this test.
        ap._LOW_CACHE_WARNING_EMITTED.clear()
        client = ap.AnthropicClient.__new__(ap.AnthropicClient)
        # Emitted at DEBUG (not WARNING) so it doesn't bleed onto the consumer
        # CLI's terminal mid-run; capture at DEBUG to assert it still fires.
        with caplog.at_level(logging.DEBUG, logger=ap.__name__):
            client._build_request(
                system="short system prompt",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"name": "x", "input_schema": {}}],
                max_tokens=1024,
                thinking_budget=None,
                model="claude-sonnet-4-6",
            )
        assert any("prompt cache no-op" in r.message for r in caplog.records), (
            "expected low-cache warning to be logged"
        )
        ap._LOW_CACHE_WARNING_EMITTED.clear()

    def test_warning_emitted_only_once_per_model(self, caplog):
        from maverick.providers import anthropic_provider as ap
        ap._LOW_CACHE_WARNING_EMITTED.clear()
        client = ap.AnthropicClient.__new__(ap.AnthropicClient)
        with caplog.at_level(logging.DEBUG, logger=ap.__name__):
            for _ in range(5):
                client._build_request(
                    system="short",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    max_tokens=1024,
                    thinking_budget=None,
                    model="claude-sonnet-4-6",
                )
        cache_warnings = [r for r in caplog.records if "prompt cache" in r.message]
        assert len(cache_warnings) == 1
        ap._LOW_CACHE_WARNING_EMITTED.clear()
