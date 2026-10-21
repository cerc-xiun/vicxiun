# Security audit — one test per rule

Ticket #17. The spec ([issue #1](https://github.com/cerc-xiun/vicxiun/issues/1))
makes security claims; this audit turns them into checkable facts. Every rule
is enumerated below with the test or tests that prove it, so "secure" is an
auditable statement rather than an opinion.

Running the audit is a single command:

```console
uv run pytest tests/test_security.py
```

That runs the dedicated security test file — one test per rule, ten in total,
plus the argument-injection surface regression. The file is also part of the
normal suite (`uv run pytest`), so CI re-proves every rule on every push.

The rule numbering follows the spec's "Where each security surface is tested"
table, which names rules 1, 2, 3, 5, 6, 7 and 9. Two rules the table leaves
implicit are named here for the first time: rule 4 (credentials) and rule 8
(title containment). Rule 10 is the audit itself.

**Audit result: all ten rules have at least one test.** One gap was found and
filled by this audit: rule 4 (credentials never requested or stored) had no
test anywhere in the suite; `test_rule_4_credentials_are_never_requested_or_stored`
was added for it. Everything else was already covered; the audit re-asserts
each rule so the guarantee is checkable in one place.

## The hostile surfaces

| Surface | Dedicated test | Independence |
| --- | --- | --- |
| **Argument injection** | `tests/test_security.py::test_surface_argument_injection_never_reaches_a_shell`, `tests/test_plan.py::test_resolve_plan_rejects_shell_metacharacters`, `tests/test_plan.py::test_resolve_plan_does_not_treat_dash_prefix_as_flag` | Rejection is asserted at the link-validation boundary with a resolver that must never be reached; a source-level guard asserts no module spawns a shell and the engine spawns nothing at all. |
| **Path traversal** | `tests/test_paths.py::test_resolve_target_contains_every_hostile_input`, `tests/test_paths.py::test_resolve_target_contains_path_independently_of_sanitisation` | Containment is asserted with the sanitiser replaced by the identity function, so it cannot silently depend on the transform. |
| **Filename sanitisation** | `tests/test_paths.py` layer 1 (`test_sanitize_*`) | Asserted on the transformed name itself, separately from the containment assertion. |

## The ten rules

| # | Rule | Where it comes from | Proven by |
| --- | --- | --- | --- |
| 1 | No code is fetched and executed at runtime | [ADR-0004](adr/0004-no-runtime-code-downloads.md); user story 38 | `tests/test_security.py::test_rule_1_no_code_is_fetched_and_executed_at_runtime`; `tests/test_engine.py::test_default_engine_disables_remote_component_fetching`; `tests/test_engine_execute.py::test_safe_default_options_disable_remote_components`, `::test_executor_options_carry_the_safety_defaults` |
| 2 | No administrator rights; `PATH` is never written; the registry is never touched | User stories 39 and 40 | `tests/test_security.py::test_rule_2_no_admin_rights_no_path_mutation_no_registry`; `tests/test_prereqs.py::test_prereqs_module_never_writes_to_PATH`, `::test_prereqs_module_never_imports_winreg`, `::test_prereqs_module_never_uses_ctypes_for_admin_checks`, `::test_run_does_not_mutate_the_live_environment` |
| 3 | Nothing is installed silently | [ADR-0005](adr/0005-nothing-installed-silently.md); user story 37 | `tests/test_security.py::test_rule_3_nothing_is_installed_silently`; `tests/test_prereqs.py::test_run_never_spawns_an_installer_subprocess`, `::test_probe_never_runs_an_installer` |
| 4 | Credentials are never requested or stored | User story 44 | `tests/test_security.py::test_rule_4_credentials_are_never_requested_or_stored` (added by this audit; no test existed before) |
| 5 | Only `http` and `https` links are accepted | User story 42 | `tests/test_security.py::test_rule_5_only_http_and_https_links_are_accepted`; `tests/test_plan.py::test_resolve_plan_accepts_http_and_https`, `::test_resolve_plan_rejects_non_http`; `tests/test_dry_run.py::test_dry_run_rejects_hostile_links_with_user_error_code` |
| 6 | Failures are never swallowed | User story 36 | `tests/test_security.py::test_rule_6_failures_are_never_swallowed`; `tests/test_queue.py::test_a_failed_item_does_not_stop_the_rest`, `::test_the_summary_lists_what_succeeded_what_failed_and_why`; `tests/test_execute.py::test_real_run_failure_carries_a_named_cause`; `tests/test_engine_execute.py::test_translate_download_error_never_returns_silently` |
| 7 | No telemetry; no self-update check | User story 41 | `tests/test_security.py::test_rule_7_no_telemetry`; `tests/test_engine.py::test_default_engine_disables_telemetry`, `::test_default_engine_disables_update_check`; `tests/test_engine_execute.py::test_safe_default_options_disable_telemetry`, `::test_safe_default_options_disable_update` |
| 8 | A hostile title cannot write outside the chosen folder | User story 43 | `tests/test_security.py::test_rule_8_a_hostile_title_cannot_write_outside_the_chosen_folder`; the `tests/test_paths.py` containment tests named under "Path traversal" above |
| 9 | The phone-safe default caps resolution | [ADR-0002](adr/0002-phone-safe-defaults.md) | `tests/test_security.py::test_rule_9_the_phone_safe_default_caps_resolution`; `tests/test_profiles.py::test_phone_safe_caps_resolution_at_1080`, `::test_phone_safe_does_not_reach_for_4k`, `::test_phone_safe_selects_h264_aac_at_1080p_or_below` |
| 10 | Every rule has a test | This audit | `tests/test_security.py::test_rule_10_every_rule_has_a_test` — fails if a rule test is deleted or this document drifts from the tests |
