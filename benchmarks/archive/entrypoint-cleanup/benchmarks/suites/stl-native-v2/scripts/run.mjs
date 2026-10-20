// Compatibility entry point; orchestration belongs to benchmarks/scripts/run.py.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { assertExecutionAllowed } from '../../../scripts/execution-control.mjs';
await assertExecutionAllowed();
const script = fileURLToPath(new URL('../../../scripts/run.py', import.meta.url));
const args = process.argv.slice(2);
if (args[0] && !args[0].startsWith('--')) args.splice(0,1,'--experiment',args[0]);
const child = spawn('/usr/bin/python3',[script,'run','--suite','stl-native-v2',...args],{stdio:'inherit'});
child.on('close',code=>{process.exitCode=code??1;});
