import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

export async function assertExecutionAllowed() {
  const root = fileURLToPath(new URL('../..', import.meta.url)).replace(/\/$/, '');
  const control = JSON.parse(await readFile(root + '/benchmarks/config/execution.json', 'utf8'));
  if (control.experiments_stopped && process.env.REA_EXPLICIT_RUN_AUTHORIZED !== '1') {
    throw new Error('Experiments are stopped. Use bin/benchmark run --dry-run to inspect caches; reporting never starts models.');
  }
}
