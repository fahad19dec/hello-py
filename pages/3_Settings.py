"""Provider, model, credential and run-depth settings."""

from __future__ import annotations

import streamlit as st

from ta_ui import catalog, ui
from ta_ui.settings import (
    LANGUAGES,
    VENDOR_CHOICES,
    apply_secrets,
    env_value,
)

ui.page("Settings", "⚙️")
settings = ui.get_settings()
ui.hero("Settings", "which models think, which vendors supply the data")

secrets: dict[str, str] = st.session_state.setdefault("secrets", {})


def model_picker(label: str, mode: str, current: str, provider: str) -> str:
    """Dropdown of catalog models plus a free-text escape hatch."""
    options = catalog.model_options(provider, mode)
    values = [value for _, value in options]
    labels = {value: text for text, value in options}

    if current in values:
        index = values.index(current)
    else:
        index = values.index("custom") if "custom" in values else 0

    # The widget key carries the provider: a model ID only means something to
    # the provider that serves it, so switching provider must build a fresh
    # widget rather than re-display the previous provider's choice.
    picked = st.selectbox(
        label,
        options=values,
        index=index,
        format_func=lambda v: "Custom model ID…" if v == "custom" else labels.get(v, v),
        key=f"model_{mode}_{provider}",
    )
    if picked == "custom":
        return st.text_input(
            f"{label} — model ID",
            value=current if current not in values else "",
            placeholder="exact model id served by your provider",
            key=f"model_{mode}_{provider}_custom",
        ).strip()
    return picked


# ------------------------------------------------------------------- models tab
tab_models, tab_keys, tab_run, tab_data, tab_advanced = st.tabs(
    ["Models", "API keys", "Run depth", "Data vendors", "Advanced"]
)

with tab_models:
    provider_options = catalog.providers()
    provider_keys = [key for _, key in provider_options]
    provider_names = {key: name for name, key in provider_options}

    settings.llm_provider = st.selectbox(
        "LLM provider",
        options=provider_keys,
        index=provider_keys.index(settings.llm_provider)
        if settings.llm_provider in provider_keys
        else 0,
        format_func=lambda k: provider_names.get(k, k),
    )

    # A model ID only means something to the provider that serves it. When the
    # provider changes, fall back to the new one's defaults instead of carrying
    # the old ID over into its "custom model" box.
    previous = st.session_state.get("provider_before")
    st.session_state.provider_before = settings.llm_provider
    if previous is not None and previous != settings.llm_provider:
        for mode, attribute in (("deep", "deep_think_llm"), ("quick", "quick_think_llm")):
            options = catalog.model_options(settings.llm_provider, mode)
            setattr(settings, attribute, next((v for _, v in options if v != "custom"), ""))
        settings.backend_url = ""
        ui.set_settings(settings)

    col_deep, col_quick = st.columns(2)
    with col_deep:
        st.caption("**Deep thinker** — researchers, managers, the final decision")
        settings.deep_think_llm = model_picker(
            "Deep model", "deep", settings.deep_think_llm, settings.llm_provider
        )
    with col_quick:
        st.caption("**Quick thinker** — analysts and tool-heavy steps")
        settings.quick_think_llm = model_picker(
            "Quick model", "quick", settings.quick_think_llm, settings.llm_provider
        )

    default_url = catalog.default_backend_url(settings.llm_provider)
    settings.backend_url = st.text_input(
        "Backend URL",
        value=settings.backend_url,
        placeholder=default_url or "provider default",
        help="Leave empty to use the provider's default endpoint. Required for "
             "OpenAI-compatible servers (vLLM, LM Studio) and Azure.",
    )
    if catalog.requires_custom_url(settings.llm_provider) and not settings.backend_url.strip():
        st.warning("This provider needs an explicit endpoint URL.", icon="🔌")

    if settings.llm_provider in {"openai", "anthropic", "google"}:
        levels = {
            "openai": ["", "low", "medium", "high"],
            "anthropic": ["", "low", "medium", "high"],
            "google": ["", "minimal", "low", "medium", "high"],
        }[settings.llm_provider]
        settings.reasoning_effort = st.selectbox(
            "Reasoning / thinking level",
            options=levels,
            index=levels.index(settings.reasoning_effort)
            if settings.reasoning_effort in levels
            else 0,
            format_func=lambda v: "Provider default" if v == "" else v.title(),
        )

with tab_keys:
    st.markdown(
        "Keys entered here stay in this server process for the session — they are "
        "never written to the settings file. For a permanent setup, put them in a "
        "`.env` file next to `Home.py` or in Streamlit secrets."
    )
    key_env = catalog.api_key_env(settings.llm_provider)
    if key_env is None:
        st.info(
            f"{provider_names.get(settings.llm_provider, settings.llm_provider)} "
            "does not use a single API key env var "
            "(Bedrock uses the AWS credential chain; Ollama needs no key)."
        )
    else:
        existing = env_value(key_env)
        entered = st.text_input(
            f"{key_env}",
            value=secrets.get(key_env, ""),
            type="password",
            placeholder="already set in the environment" if existing else "paste your key",
        )
        if entered:
            secrets[key_env] = entered
        st.caption("✅ detected" if existing or secrets.get(key_env) else "⚠️ not set")

    st.markdown("#### Data vendor keys")
    for name, label, note in catalog.DATA_KEY_ENV:
        existing = env_value(name)
        entered = st.text_input(
            f"{label} · {name}",
            value=secrets.get(name, ""),
            type="password",
            placeholder="already set in the environment" if existing else note,
        )
        if entered:
            secrets[name] = entered

with tab_run:
    settings.analysts = st.multiselect(
        "Default analyst team",
        options=[key for key, _, _ in catalog.ANALYSTS],
        default=settings.analysts,
        format_func=lambda k: next(label for key, label, _ in catalog.ANALYSTS if key == k),
    )
    for _key, label, note in catalog.ANALYSTS:
        st.caption(f"**{label}** — {note}")

    col_a, col_b = st.columns(2)
    settings.max_debate_rounds = col_a.slider(
        "Research debate rounds", 1, 5, settings.max_debate_rounds
    )
    settings.max_risk_discuss_rounds = col_b.slider(
        "Risk discussion rounds", 1, 5, settings.max_risk_discuss_rounds
    )
    settings.output_language = st.selectbox(
        "Report language",
        options=LANGUAGES,
        index=LANGUAGES.index(settings.output_language)
        if settings.output_language in LANGUAGES
        else 0,
        help="Reports and the final decision are written in this language; the "
             "agents' internal debate stays in English.",
    )
    settings.save_reports = st.checkbox(
        "Save markdown reports after each run", value=settings.save_reports
    )

with tab_data:
    st.caption(
        "Each category is an exact vendor chain — requests are never silently "
        "routed elsewhere. Alpha Vantage needs `ALPHA_VANTAGE_API_KEY`."
    )
    for category, choices in VENDOR_CHOICES.items():
        current = settings.data_vendors.get(category, choices[0])
        settings.data_vendors[category] = st.selectbox(
            category.replace("_", " ").title(),
            options=choices,
            index=choices.index(current) if current in choices else 0,
            key=f"vendor_{category}",
        )

with tab_advanced:
    col_a, col_b = st.columns(2)
    use_temp = col_a.checkbox("Set temperature", value=settings.temperature is not None)
    settings.temperature = (
        col_a.slider("Temperature", 0.0, 2.0, float(settings.temperature or 0.7), 0.1)
        if use_temp
        else None
    )
    use_tokens = col_b.checkbox("Cap output tokens", value=bool(settings.max_tokens))
    settings.max_tokens = (
        col_b.number_input(
            "Max tokens", min_value=256, max_value=200_000,
            value=int(settings.max_tokens or 8000), step=256,
        )
        if use_tokens
        else None
    )

    use_retries = st.checkbox(
        "Override SDK retry budget", value=settings.llm_max_retries is not None
    )
    settings.llm_max_retries = (
        st.number_input(
            "Retries per call", min_value=0, max_value=10,
            value=int(settings.llm_max_retries if settings.llm_max_retries is not None else 2),
        )
        if use_retries
        else None
    )

    settings.checkpoint_enabled = st.checkbox(
        "Enable checkpointing",
        value=settings.checkpoint_enabled,
        help="Saves graph state after each node so a crashed run resumes from the "
             "last successful step on the next identical run.",
    )
    settings.results_dir = st.text_input(
        "Results directory",
        value=settings.results_dir,
        placeholder="~/.tradingagents/logs",
        help="Where reports and run logs are written.",
    )

st.divider()
save, reset = st.columns([1, 4])
if save.button("Save settings", type="primary"):
    ui.set_settings(settings)
    apply_secrets(secrets)
    path = settings.save()
    st.success(f"Saved to `{path}`. Keys applied to this session only.")
if reset.button("Reset to defaults"):
    from ta_ui.settings import Settings

    ui.set_settings(Settings())
    st.rerun()
