"""Focused offline request-limit presentation controls; no report rebuild or API."""
import copy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

import public_report
import report
import report_budget as budget
import records
import report_views


def completed_fixture(*, exceeded=True):
    task, receipt = fixture()
    contract = task['request_contract']
    contract['budget_semantics'] = 'complete-current-response-stop-before-next-v1'
    receipt.update(contract=copy.deepcopy(contract), contract_sha256=budget.digest(contract),
        status='completed_over_budget' if exceeded else 'wire_enforced',
        budget_exceeded=exceeded, queue_stop_required=True,
        queue_stop_reason='task_observed_token_limit_reached',
        budget_excess={'max_observed_total_tokens': {'limit': 200000, 'observed': 200001, 'excess': 1}} if exceeded else {},
        final_response_complete=True, request_ledger_complete=True, transport_complete=True,
        rejected_requests=0, requests=[{'request_id': '1', 'forwarded': True, 'response_complete': True, 'final_response': True}],
        usage_observations=[{'request_id': '1', 'complete': True,
                             'usage': {'outputTokens': 1, 'totalTokens': 200001 if exceeded else 200000}}])
    return task, receipt


def fixture(observed=False):
    contract = {'mode':'observed_task_limits' if observed else 'certified',
        'protocol':'openai-codex-responses' if observed else 'anthropic-messages',
        'cap_scope':'none' if observed else 'per_response',
        'cap_field':None if observed else 'max_tokens',
        'cap_minimum':None if observed else 1,'output_ceiling':None if observed else 8192,
        'wall_seconds':1800,'tool_calls':60,
        'task_limits':{'max_model_turns':12,'max_response_tokens':8192,
                       'max_output_tokens':32768,'max_observed_total_tokens':200000}}
    receipt = {'contract':copy.deepcopy(contract),'contract_sha256':budget.digest(contract),
        'provider_forwards':1,'status':'observed_limits_enforced' if observed else 'wire_enforced',
        'provider_output_cap_enforced':not observed}
    return {'request_contract':contract}, receipt


class BudgetReportControls(unittest.TestCase):
    def test_completed_over_budget_remains_distinct_from_compliance(self):
        task, receipt = completed_fixture()
        value = budget.summarize(task, receipt)
        self.assertEqual(value['outcome']['status'], 'completed_over_budget')
        self.assertIn('Budget exceeded / attempt stopped', budget.description(value))
        self.assertIn('does not make the attempt budget-compliant', budget.detail(value))
        self.assertEqual(budget.project(value), value)
        for flag in ('final_response_complete', 'request_ledger_complete', 'transport_complete'):
            broken = copy.deepcopy(receipt); broken[flag] = False
            self.assertNotIn('outcome', budget.summarize(task, broken))

    def test_complete_threshold_final_keeps_hold_without_claiming_excess(self):
        task, receipt = completed_fixture(exceeded=False)
        value = budget.summarize(task, receipt)
        self.assertFalse(value['outcome']['budget_exceeded'])
        self.assertIn('Budget limit reached / attempt stopped', budget.detail(value))
        self.assertNotIn('Budget exceeded / attempt stopped', budget.description(value))

    def test_native_record_table_keeps_grade_and_queue_hold(self):
        task, receipt = completed_fixture()
        with tempfile.TemporaryDirectory(prefix='rea-budget-report-') as tmp:
            directory = Path(tmp)
            for name, value in [('task.json', task), ('request-budget.json', receipt)]:
                (directory / name).write_text(json.dumps(value))
            with patch.object(records, 'ROOT', directory), patch.dict(records.INPUTS, clear=True):
                self.assertEqual(records.request_budget_state(directory, 'graded'), 'completed_over_budget')
                self.assertEqual(records.request_budget_state(directory, 'environment_failure'), 'environment_failure')
        saved = public_report.validate(json.loads(public_report.SNAPSHOT.read_text()))
        row = copy.deepcopy(next(r for r in saved['records'] + saved['condition_records'] if r['grade']))
        row.update(status='completed_over_budget', budget_condition=budget.summarize(task, receipt))
        view = public_report.PublicReport.__new__(public_report.PublicReport); view.data = saved
        rendered = view.case_table([row])
        self.assertIn('Budget exceeded / attempt stopped', rendered)
        self.assertIn(f'{row["grade"]["passed"]:,}/{row["grade"]["total"]:,}', rendered)
        self.assertEqual(public_report.project_row(row)['grade']['passed'], row['grade']['passed'])

    def test_typed_outcome_labels_correctness_and_spending_separately(self):
        task, receipt = completed_fixture()
        row = {'status': 'completed_over_budget', 'budget_outcome': budget.outcome(receipt, task['request_contract']),
               'grade': {'artifact_valid': True, 'task_success': True, 'return_provenance': 'host_emulator_return_v1', 'passed': 7, 'total': 7}}
        state, _, score = report_views.typed_outcome(row)
        self.assertIn('All checks passed', state)
        self.assertIn('Budget exceeded / attempt stopped', state)
        self.assertEqual(score, '7/7 strict task points')

    def test_final_held_cell_is_incomplete_even_without_pending_cells(self):
        status = {'selected_tasks': 1, 'provider_launches': 1, 'model_api_calls': 1,
                  'failures': [], 'unprocessed_tasks': 0,
                  'queue_holds': [{'model': 'glm-5.3', 'case': 'fixture', 'reason': 'task_observed_token_limit_reached'}],
                  'timings': {'schema_version': 1, 'total_seconds': 1, 'stages': []}}
        value = report.benchmark_timing_summary(status)
        self.assertEqual(value['status'], 'incomplete')
        self.assertEqual(value['queue_holds'], 1)
        self.assertNotIn('glm', json.dumps(value))
        public_report.validate_benchmark_timing(value)

    def test_public_projection_keeps_over_budget_grade_and_rejects_erased_stop(self):
        saved = public_report.validate(json.loads(public_report.SNAPSHOT.read_text()))
        row = next(r for r in saved['records'] + saved['condition_records'] if r['grade'])
        original_grade = copy.deepcopy(row['grade'])
        task, receipt = completed_fixture()
        row.update(status='completed_over_budget', budget_condition=budget.summarize(task, receipt))
        public_report.validate(saved)
        self.assertEqual(row['grade'], original_grade)
        row['status'] = 'graded'
        with self.assertRaisesRegex(ValueError, 'queue-hold state'):
            public_report.validate(saved)
        row['status'] = 'completed_over_budget'
        row['budget_condition']['outcome']['budget_exceeded'] = False
        with self.assertRaisesRegex(ValueError, 'budget outcome'):
            public_report.validate(saved)

    def test_missing_historical_contract_stays_unknown(self):
        self.assertEqual(budget.summarize({}, {'status':'wire_enforced'}),budget.unknown())
        self.assertIn('does not certify historical',budget.detail(None))

    def test_capped_contract_and_saved_execution_distinguished(self):
        task,receipt=fixture()
        declared=budget.summarize(task,{})
        self.assertIsNone(declared['provider_output_cap_enforced'])
        self.assertEqual(declared['evidence'],'recorded_contract')
        observed=budget.summarize(task,receipt)
        self.assertTrue(observed['provider_output_cap_enforced'])
        self.assertEqual(observed['per_response_output_tokens'],8192)
        self.assertEqual(budget.validate(observed),observed)

    def test_codex_observed_rules_never_claim_response_cap(self):
        task,receipt=fixture(True)
        for status in ('observed_limits_enforced','budget_exhausted'):
            receipt['status']=status
            value=budget.summarize(task,receipt)
            self.assertEqual(value['evidence'],'recorded_request_receipt')
            self.assertFalse(value['provider_output_cap_enforced'])
            self.assertIsNone(value['per_response_output_tokens'])
            self.assertEqual(value['max_model_turns'],12)
            self.assertEqual(value['max_task_output_tokens'],32768)
            self.assertEqual(budget.validate(value),value)
            text=budget.detail(value)
            for part in ('one response can exceed','Missing usage stops','actual subscription debit is unknown'):
                self.assertIn(part,text)

    def test_changed_receipt_cannot_certify_execution(self):
        task,receipt=fixture()
        receipt['contract_sha256']='0'*64
        value=budget.summarize(task,receipt)
        self.assertEqual(value['evidence'],'recorded_contract')
        self.assertIsNone(value['provider_output_cap_enforced'])

    def test_projection_drops_raw_and_rejects_false_cap(self):
        task,receipt=fixture(True)
        task['auth_token']='private-auth';receipt['raw_request']={'prompt':'private-reasoning'}
        value=budget.summarize(task,receipt)
        value['raw_request']='private-reasoning'
        projected=budget.project(value)
        self.assertNotIn('private',json.dumps(projected))
        broken=copy.deepcopy(projected);broken['provider_output_cap_enforced']=True
        with self.assertRaisesRegex(ValueError,'cannot claim'):
            budget.validate(broken)

    def test_public_schema_preserves_old_and_allows_safe_additions(self):
        saved=json.loads(public_report.SNAPSHOT.read_text())
        saved.pop('typed_budget_conditions',None)
        for row in saved['records']+saved['condition_records']:
            row.pop('budget_condition',None)
            # A pre-budget-schema fixture cannot contain a newer status that
            # requires a saved budget receipt. Never weaken production validation.
            if row['status'] == 'completed_over_budget':
                row['status'] = 'graded'
        old=public_report.validate(copy.deepcopy(saved))
        self.assertNotIn('typed_budget_conditions',old)
        augmented=copy.deepcopy(saved)
        task,receipt=fixture(True);value=budget.summarize(task,receipt)
        augmented['records'][0]['budget_condition']=value
        key=augmented['capability_measurements']['history'][0]['attempt_id']
        augmented['typed_budget_conditions']={key:copy.deepcopy(value)}
        result=public_report.validate(augmented)
        self.assertFalse(result['typed_budget_conditions'][key]['provider_output_cap_enforced'])
        augmented['records'][0]['budget_condition']['credential']='secret'
        with self.assertRaises(ValueError):
            public_report.validate(augmented)

    def test_cohort_note_does_not_claim_equal_budgets(self):
        task,receipt=fixture(True)
        rows=[{'model':'gpt','grade':{'passed':1,'total':1},'budget_condition':budget.summarize(task,receipt)},
              {'model':'old','grade':{'passed':1,'total':1}}]
        text=budget.comparison_note(rows,{'gpt':{'label':'GPT'},'old':{'label':'Historical'}})
        for part in ('not request budgets','no provider response cap','Budget condition unknown','not equal-budget rankings'):
            self.assertIn(part,text)

    def test_configured_policy_text_distinguishes_routes(self):
        view=report.Report.__new__(report.Report)
        view.data={'task_budget_policy':{'policy_id':'fixture','limits':fixture()[0]['request_contract']['task_limits']}}
        text=view.task_budget_section()
        for part in ('provider-capped routes only','sends no provider response cap','single response can exceed','actual Codex subscription debit is unknown'):
            self.assertIn(part,text)


if __name__=='__main__':
    unittest.main(verbosity=2)
