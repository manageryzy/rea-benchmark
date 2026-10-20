import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

// Configuration is trusted coordinator input, never a model-supplied path.
export async function loadToolCatalog(root, config) {
  const path = resolve(root, config.analysis_tools_file ?? 'benchmarks/config/rea-tools.json');
  return { path, catalog: JSON.parse(await readFile(path, 'utf8')) };
}

export function restrictDataQuery(name, args, config) {
  if (args.document !== undefined) throw new Error('Document overrides are unavailable in this benchmark');
  if (name === 'read_bytes') {
    args.length ??= 64;
    const maximum = config.data_limits?.max_read_bytes ?? 4096;
    if (!Number.isSafeInteger(args.length) || args.length < 1 || args.length > maximum)
      throw new Error(`read_bytes length must be between 1 and ${maximum}`);
  }
  if (name === 'search_bytes') {
    args.limit ??= 50;
    const maximum = config.data_limits?.max_search_hits ?? 200;
    if (!Number.isSafeInteger(args.limit) || args.limit < 1 || args.limit > maximum)
      throw new Error(`search_bytes limit must be between 1 and ${maximum}`);
  }
  return args;
}
