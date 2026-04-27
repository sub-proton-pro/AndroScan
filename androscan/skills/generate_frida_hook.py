"""LLM-requestable skill: render a Frida hook from a v1 template.

Sub-step 4.7 (DEC-023). The LLM picks a ``template_id`` from the
hook-library catalog and supplies the parameter dict; this skill calls
:func:`androscan.adapters.frida_hooks.render_by_id` and packages the
result. The template library is the **only** source of hook JS in v1
— the LLM fills parameters, never emits raw JS — per DEC-023's
hook-source policy.

Consent class
-------------
``requires_confirmation=True`` (DEC-022's consent-class hook). This
skill is the first real ``True`` consumer of that flag in v1: the
chat agentic loop must surface a Stage / Allow prompt to the operator
before the rendered JS is offered for injection. **The skill itself
never injects** — it's headless prep, returning the rendered JS and a
deterministic pentester summary so the operator can review both before
hitting Inject in the Hook Lab UI (4.5). Skipping straight from "LLM
picked a template" to "Frida loadScript" without operator review would
violate DEC-023's Option-A confirmation UX.

Output shape
------------
On success::

    SkillResult(
        success=True,
        data={
            "template_id": "<id>",
            "js": "<rendered Frida JS>",
            "summary": "<deterministic pentester summary, post-substitution>",
            "params_used": {...},        # operator-supplied + filled defaults
            "sensitive_apis": [...],     # informational, mirrors HookTemplate field
            "rationale": "<optional LLM-supplied 'why' string>",
        },
        text=<human-readable preview combining summary + first few JS lines>,
    )

On failure (unknown template / missing-required / unknown-key) the
skill returns ``success=False`` with a clean ``text`` that quotes the
underlying :class:`HookTemplateError` message; the schema-aware
template error already lists the valid template ids / declared params
so the LLM can self-correct on the next turn.
"""

from __future__ import annotations

from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult

SKILL_META = SkillMeta(
    name="generate_frida_hook",
    description=(
        "Render a Frida hook from a v1 hook-library template by id, with "
        "operator-supplied parameters. Returns the rendered JavaScript plus a "
        "deterministic pentester-summary preview the operator must review and "
        "explicitly stage / inject from the Hook Lab UI. The skill itself does "
        "NOT attach to a process or inject the script. Consent-class skill "
        "(requires_confirmation=True per DEC-022)."
    ),
    params_schema={
        "template_id": (
            "id of the hook-library template (one of: 'entry_exit_log', "
            "'scope_inspector', 'ssl_pinning_bypass', 'crypto', "
            "'shared_preferences', 'intent'). Required."
        ),
        "params": (
            "dict of template parameters keyed by the schema's parameter "
            "names. Each template declares its own schema; e.g. "
            "entry_exit_log requires {class_name, method_name, event_label}. "
            "Required (use {} for templates that have only optional params)."
        ),
        "rationale": (
            "optional human-readable string explaining *why* this hook is "
            "being proposed. Echoed back in the result so the operator's "
            "consent prompt has context. Free-form; not used by the renderer."
        ),
    },
    tier="llm",
    requires_confirmation=True,
)

_JS_PREVIEW_LINES = 8
_PREVIEW_MAX_CHARS = 600


def _format_preview(rendered_js: str) -> str:
    lines = rendered_js.splitlines()
    head = "\n".join(lines[:_JS_PREVIEW_LINES])
    if len(lines) > _JS_PREVIEW_LINES:
        head += f"\n  … (+{len(lines) - _JS_PREVIEW_LINES} more line(s))"
    if len(head) > _PREVIEW_MAX_CHARS:
        head = head[: _PREVIEW_MAX_CHARS - 1] + "…"
    return head


def execute(params: dict, context: SkillContext) -> SkillResult:
    del context  # The skill is pure: no run-folder / dossier dependency.

    template_id = (params.get("template_id") or "").strip()
    if not template_id:
        return SkillResult(
            success=False,
            data=None,
            text="[generate_frida_hook] 'template_id' is required.",
        )

    raw_template_params: Any = params.get("params")
    if raw_template_params is None:
        template_params: dict[str, Any] = {}
    elif isinstance(raw_template_params, dict):
        template_params = dict(raw_template_params)
    else:
        return SkillResult(
            success=False,
            data=None,
            text=(
                "[generate_frida_hook] 'params' must be a dict mapping "
                "template-schema names to values; got "
                f"{type(raw_template_params).__name__}."
            ),
        )

    rationale = (params.get("rationale") or "").strip() or None

    try:
        from androscan.adapters.frida_hooks import (
            HookParamError,
            HookTemplateNotFound,
            get_template,
            render_by_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return SkillResult(
            success=False,
            data=None,
            text=f"[generate_frida_hook] hook-library unavailable: {exc}",
        )

    try:
        rendered = render_by_id(template_id, template_params)
    except HookTemplateNotFound as exc:
        return SkillResult(
            success=False,
            data=None,
            text=f"[generate_frida_hook] {exc}",
        )
    except HookParamError as exc:
        # Re-fetch the template (if we can) so the failure message can
        # remind the LLM of the declared schema. ``HookTemplateNotFound``
        # short-circuits above, so this lookup either succeeds or the
        # original error is plenty.
        schema_hint: Optional[str] = None
        try:
            tmpl = get_template(template_id)
            declared = ", ".join(
                f"{p.name}{'' if p.required else f'={p.default!r}?'}"
                for p in tmpl.params
            )
            if declared:
                schema_hint = f" Declared schema: ({declared})."
        except HookTemplateNotFound:
            pass
        return SkillResult(
            success=False,
            data=None,
            text=f"[generate_frida_hook] {exc}.{schema_hint or ''}",
        )

    template = get_template(template_id)
    sensitive_apis = list(template.sensitive_apis)

    text_lines = [
        f"[generate_frida_hook] Rendered template {template_id!r} "
        f"({len(rendered.js.splitlines())} JS line(s), "
        f"{len(rendered.summary.splitlines())} summary line(s)).",
        "",
        "Pentester summary:",
        rendered.summary.strip() or "(no summary)",
        "",
        "JS preview:",
        _format_preview(rendered.js),
    ]
    if sensitive_apis:
        text_lines.append("")
        text_lines.append("Sensitive APIs touched: " + ", ".join(sensitive_apis))
    if rationale:
        text_lines.append("")
        text_lines.append(f"Rationale: {rationale}")
    text_lines.append("")
    text_lines.append(
        "Operator action required: review the JS + summary above, then "
        "stage / inject from the Hook Lab UI. This skill does not attach to a "
        "process or inject the script."
    )

    return SkillResult(
        success=True,
        data={
            "template_id": rendered.template_id,
            "js": rendered.js,
            "summary": rendered.summary,
            "params_used": dict(rendered.params_used),
            "sensitive_apis": sensitive_apis,
            "rationale": rationale,
        },
        text="\n".join(text_lines),
    )
