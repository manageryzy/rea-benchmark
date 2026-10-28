"""Native report outcomes shared by tables, charts and public validation.

An ungraded terminal state remains an observation, never a correctness zero.
"""
STATES = {
    'graded': 'Graded',
    'localization_format_review': 'Localization format needs review; semantic result ungraded',
    'inference_saved_grading_pending': 'Inference saved; grading pending',
    'interrupted': 'Interrupted; preserved',
    'running': 'Running',
    'not_started': 'Not started',
    'environment_failure': 'Harness/provider failure; not correctness-graded',
    'budget_exhausted': 'Task budget exhausted; preserved without retry',
    'completed_over_budget': 'Complete answer; budget exceeded / queue held',
    'completed_queue_held': 'Complete answer; budget limit reached / queue held',
}

# Compatibility name for existing projection/table callers.
LABELS = STATES
