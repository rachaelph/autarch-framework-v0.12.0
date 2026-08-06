"""Same extraction as ``examples/extract.py`` — but the REASONING runs on the MICROSOFT AGENT FRAMEWORK.

This is deliberately ``examples/extract.py`` with ONE thing swapped: the model. autarch's whole
pipeline talks to intelligence through a single seam — ``ModelProvider.complete(prompt, system)`` —
so we inject a :class:`autarch.MAFModelProvider` (an autarch provider whose completions are produced
by an ``agent_framework.Agent`` on Azure OpenAI) and reuse the ENTIRE extract.py pipeline unchanged:

  * the governed, signed document read;
  * multi-project identification and PER-PROJECT breakdown (type -> components -> impact factors ->
    VEC -> sensitivity indicators), reference-DB-authoritative with intelligent AI fallback;
  * anti-hallucination grounding, the quality panel, the safety panel;
  * the downloadable HTML report (``--html``).

Every one of those model calls now flows through the Microsoft Agent Framework, while autarch keeps
governing (only ``doc.read`` is granted, the read is signed, values are grounded and scored). autarch
is still the single import; MAF is the pluggable reasoning engine — swap ``MAFModelProvider`` for a
LangChain-backed provider and the very same pipeline runs on LangChain instead.

Usage (identical flags to extract.py, plus ``--auth``):
    python examples/extract_maf.py "C:/path/to/document.pdf" --model azure:gpt-5.4 --auth aad
    python examples/extract_maf.py "C:/path/to/document.pdf" --model azure:gpt-5.4 --html

Needs:  pip install agent-framework
        AZURE_OPENAI_ENDPOINT  and either AZURE_OPENAI_API_KEY or `az login` (Entra ID, --auth aad)
        the deployment via --model azure:<deployment>  (or AZURE_OPENAI_DEPLOYMENT).
Without Azure configured it runs the SAME pipeline offline on the deterministic mock provider.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Run against THIS repository's autarch (the copy that ships MAFModelProvider) and its extract.py,
# regardless of any other autarch install that may be on the path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "examples"))

import extract  # the full pipeline — reused verbatim  # noqa: E402
import autarch.intelligence.factory as autarch_factory  # noqa: E402
from autarch import MAFModelProvider  # noqa: E402

try:
    import agent_framework as af  # noqa: F401
    _MAF = True
except Exception:  # pragma: no cover
    _MAF = False


def _banner(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def _split_flag(argv, flag):
    """Pop ``flag <value>`` from a copy of argv; return ``(value, remaining_argv)``."""
    rest = list(argv)
    value = None
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            value = rest[i + 1]
            del rest[i : i + 2]
        else:
            del rest[i]
    return value, rest


def _set_model(argv, spec):
    out = list(argv)
    if "--model" in out:
        out[out.index("--model") + 1] = spec
    else:
        out += ["--model", spec]
    return out


def _is_auth_error(exc) -> bool:
    m = str(exc).lower()
    return any(s in m for s in (
        "authenticationtypedisabled", "key based authentication is disabled",
        "permissiondenied", "invalid api key", "access denied", "code: 401", "code: 403",
        "401", "403",
    ))


def make_client_factory(deployment: str, endpoint: str, api_version: str, use_aad: bool):
    """Return ``(factory, auth_label)``. ``factory()`` builds a MAF Azure chat client on demand.

    It is called lazily on the provider's own event loop, so the async transport is created and
    used on exactly one loop (safe to reuse across autarch's thread-pool workers). Entra ID is used
    when ``use_aad`` is set or no API key is present; it needs ``azure-identity`` + ``az login`` (or a
    managed identity) with the *Cognitive Services OpenAI User* role on the resource.
    """
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    prefer_aad = use_aad or not api_key

    def _factory():
        from openai import AsyncAzureOpenAI
        from agent_framework.openai import OpenAIChatCompletionClient

        kwargs = dict(azure_endpoint=endpoint, api_version=api_version)
        if prefer_aad:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        else:
            kwargs["api_key"] = api_key
        azure = AsyncAzureOpenAI(**kwargs)
        return OpenAIChatCompletionClient(model=deployment, async_client=azure)

    return _factory, ("Entra ID" if prefer_aad else "api-key")


def _connect_provider(deployment, endpoint, api_version, auth_mode):
    """Build + validate a MAFModelProvider, auto-falling-back between api-key and Entra ID.

    Returns ``(provider, auth_label)`` or ``(None, None)`` if no auth method could connect.
    """
    has_key = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
    modes = {"aad": [True], "key": [False]}.get(auth_mode, [False, True] if has_key else [True])
    for idx, use_aad in enumerate(modes):
        factory, label = make_client_factory(deployment, endpoint, api_version, use_aad)
        candidate = MAFModelProvider(factory, agent_name="autarch-maf-extractor", model_label=deployment)
        try:
            # Probe: builds the client on the loop and validates auth with one tiny turn.
            candidate.complete("Reply with the single word: OK.")
            return candidate, label
        except Exception as exc:  # noqa: BLE001
            candidate.close()
            if _is_auth_error(exc) and idx + 1 < len(modes):
                other = "Entra ID" if not use_aad else "api-key"
                print(f"  {label} auth rejected by the resource — retrying with {other} ...")
                continue
            print(f"  MAF/Azure connection failed ({type(exc).__name__}: {exc})")
            return None, None
    return None, None


def main() -> int:
    argv = sys.argv[1:]
    auth_flag, argv = _split_flag(argv, "--auth")
    auth_mode = (auth_flag or os.environ.get("AZURE_OPENAI_AUTH") or "auto").lower()
    auth_mode = {"entra": "aad", "ad": "aad", "azuread": "aad", "apikey": "key"}.get(auth_mode, auth_mode)

    if not _MAF:
        print("Microsoft Agent Framework is not installed:  pip install agent-framework")
        return 2

    # The model spec (for display + deployment resolution). extract.py re-parses argv itself.
    model_spec, _ = _split_flag(argv, "--model")
    model_spec = model_spec or "azure:gpt-5.4"
    deployment = (
        model_spec.split("azure:", 1)[1] if model_spec.startswith("azure:")
        else (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model_spec)
    )
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

    _banner("MICROSOFT AGENT FRAMEWORK — reasoning engine  |  autarch — governance")
    print("  Same pipeline as examples/extract.py; every model call runs on a MAF agent, governed by autarch.")

    if not endpoint:
        print("\n  Azure not configured (set AZURE_OPENAI_ENDPOINT + auth to drive it live via MAF).")
        print("  Running the SAME pipeline offline on the deterministic mock provider so you can see the output.\n")
        sys.argv = [sys.argv[0]] + _set_model(argv, "mock")
        return extract.main()

    provider, label = _connect_provider(deployment, endpoint, api_version, auth_mode)
    if provider is None:
        print("\n  Could not reach Azure via the Microsoft Agent Framework.")
        print("  Falling back to the offline mock provider so you can still see the pipeline.\n")
        sys.argv = [sys.argv[0]] + _set_model(argv, "mock")
        return extract.main()

    print(f"  reasoning engine: Microsoft Agent Framework on '{deployment}' (api-version {api_version}, auth {label})")

    # THE swap: every build_provider(...) now returns our MAF-backed provider, so the Microsoft
    # Agent Framework drives ALL reasoning while autarch governs. We patch it in two places:
    #   * extract._base_build_provider — the factory seam extract.py's build_provider() wraps, so the
    #     --lang output decorator still applies to the MAF provider for extraction/derivation calls;
    #   * autarch.intelligence.factory.build_provider — the source the LLM judges (RubricJudge in
    #     the quality/safety panels) import lazily. Without the second patch the judges would fall
    #     back to autarch's own Azure provider and fail (accuracy/coherence/harmful_content).
    def _maf_build_provider(spec=None, *, resilient=True):  # matches build_provider's signature
        return provider

    original_extract_bp = extract._base_build_provider
    original_factory_bp = autarch_factory.build_provider
    extract._base_build_provider = _maf_build_provider  # patch the SEAM so extract.build_provider's
    autarch_factory.build_provider = _maf_build_provider  # --lang decorator still wraps the provider
    try:
        sys.argv = [sys.argv[0]] + argv  # forward the user's flags (path, --model, --html, --shapes, ...)
        return extract.main()
    finally:
        extract._base_build_provider = original_extract_bp
        autarch_factory.build_provider = original_factory_bp
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
