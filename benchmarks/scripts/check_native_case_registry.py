"""Focused offline controls for config-driven native case registration."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import benchmark_config
import native_case_registry as registry
import records


class NativeCaseRegistryControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rea-native-registry-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry_path = self.root / 'benchmarks/config/extensions.json'
        self.public = self.root / 'benchmarks/suites/record-parser/public/build-points/cpp--clang20--O3'
        self.public.mkdir(parents=True)
        self.build = {'compiler_id':'clang20','compiler_family':'Clang','compiler_version':'clang 20',
            'optimization':'O3','standard':'c++20','string_abi':1,'standard_library':'libstdc++ 13','strip_mode':'debug'}
        self.item = {'experiment':'native-pilot','suite':'record-parser','id':'cpp--clang20--O3','base_case':'cpp',
            'public_directory':str(self.public.relative_to(self.root)),
            'build_receipt':'benchmarks/suites/record-parser/private/build-points/example/validation.json',
            'target_build':self.build}
        (self.public/'target').write_bytes(b'frozen ELF fixture')
        (self.public/'prompt.txt').write_bytes(b'Recover the original C++ behavior.')
        digest = lambda raw:hashlib.sha256(raw).hexdigest()
        manifest = {'id':self.item['id'],'binary_sha256':digest((self.public/'target').read_bytes()),**self.build}
        (self.public/'manifest.json').write_text(json.dumps(manifest))
        self.item['inputs_sha256'] = {name:digest((self.public/name).read_bytes()) for name in ('target','prompt.txt','manifest.json')}
        receipt = {'status':'locally_verified','point_id':self.item['id'],
            **{key:self.item[key] for key in ('experiment','suite','base_case','public_directory')},**self.build,
            'binary_sha256':self.item['inputs_sha256']['target'],
            'artifacts_sha256':{self.item['public_directory']+'/'+key:value for key,value in self.item['inputs_sha256'].items()}}
        receipt_path = self.root/self.item['build_receipt'];receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(json.dumps(receipt))
        self.item['build_receipt_sha256']=digest(receipt_path.read_bytes())
        self.value={'schema_version':1,'compiler_matrix_config':'benchmarks/config/cpp-build-points.json','cases':[self.item]}
        self.registry_path.parent.mkdir(parents=True)
        self.write()
        self.enterContext(patch.object(records,'ROOT',self.root))
        self.enterContext(patch.object(records,'registry',return_value={'native_case_extensions':str(self.registry_path.relative_to(self.root))}))
        self.experiment={'id':'native-pilot','suite':'record-parser','role':'primary'}
        self.base=[{'id':'cpp','language':'cpp','max_tool_calls':20,'timeout_seconds':900,'max_output_tokens':8192,
                    'experiment':'native-pilot','suite':'record-parser'}]

    def write(self):
        self.registry_path.write_text(json.dumps(self.value))

    def case(self):
        return registry.extensions(self.experiment,self.base)[0]

    def test_config_adds_variant_without_mutating_base(self):
        before=copy.deepcopy(self.base);case=self.case()
        self.assertEqual(self.base,before)
        self.assertEqual(case['max_tool_calls'],20)
        self.assertEqual(case['standard'],'c++20')
        self.assertEqual(registry.public_directory(case),self.public)

    def test_payload_mutation_rejected(self):
        case=self.case();(self.public/'target').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'public input changed'):
            registry.public_directory(case)

    def test_receipt_mutation_rejected(self):
        case=self.case();(self.root/self.item['build_receipt']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'receipt changed'):
            registry.public_directory(case)

    def test_duplicate_or_base_relabel_rejected(self):
        self.value['cases'].append(copy.deepcopy(self.item));self.write()
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            registry.configuration()
        self.value['cases']=[copy.deepcopy(self.item)];self.value['cases'][0]['id']='cpp';self.write()
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            registry.configuration()

    def test_escape_and_wrong_family_rejected(self):
        self.value['cases'][0]['public_directory']='../outside';self.write()
        with self.assertRaisesRegex(ValueError,'escapes'):
            registry.configuration()
        self.value['cases'][0]['public_directory']=self.item['public_directory']
        # item and registry share the original mapping: restore the known path.
        self.value['cases'][0]['public_directory']=str(self.public.relative_to(self.root))
        self.value['cases'][0]['suite']='wrong-suite';self.write()
        with self.assertRaisesRegex(ValueError,'existing base case'):
            self.case()

    def test_deferred_path_confinement_rejects_symlink_escape_at_payload_use(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / 'escape'
            link.symlink_to(outside, target_is_directory=True)
            self.value['cases'][0]['public_directory'] = 'escape'
            self.write()
            # Declaration-only enumeration is not payload admission.
            case = self.case()
            with self.assertRaisesRegex(ValueError, 'escapes'):
                registry.public_directory(case)

    def test_selection_flag_and_registered_matrix(self):
        registry.require_matrix(self.value['compiler_matrix_config'])
        with self.assertRaisesRegex(ValueError,'registered'):
            registry.require_matrix('other.json')
        selection={'models':'all','suites':'all','experiments':'primary','cases':'all','compiler_matrix':{'inference_enabled':False}}
        entry={'model':'any','suite':'record-parser','experiment':'native-pilot','case':self.item['id'],'native_case_origin':'compiler_matrix'}
        self.assertFalse(benchmark_config.accepts(selection,entry))
        selection['compiler_matrix']['inference_enabled']=True
        self.assertTrue(benchmark_config.accepts(selection,entry))
        del entry['native_case_origin'];selection['compiler_matrix']['inference_enabled']=False
        self.assertTrue(benchmark_config.accepts(selection,entry))


if __name__=='__main__':
    unittest.main(verbosity=2)
