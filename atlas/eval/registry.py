"""Trusted hidden check registry, not selected shell commands from a dataset."""
from pathlib import Path

GRADERS = {
    'usage-json-v1': ('usage_json.py', ('quoted_json_path', 'invalid_arguments_unknown', 'not_confirmed_read')),
    'effective-links-v1': ('effective_links.py', ('replaced_target_not_read', 'replacement_rejected')),
    'effective-budget-v1': ('effective_budget.py', ('bounded_read', 'partial_evidence', 'zero_budget_no_open')),
}


def grader_path(grader_id):
    try:
        return Path(__file__).with_name('graders') / GRADERS[grader_id][0]
    except KeyError as error:
        raise ValueError('unknown trusted grader') from error
