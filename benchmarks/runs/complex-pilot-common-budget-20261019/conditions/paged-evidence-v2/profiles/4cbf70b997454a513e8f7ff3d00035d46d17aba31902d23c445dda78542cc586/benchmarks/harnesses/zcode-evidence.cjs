'use strict';
// Benchmark-only source overlay. The installed CLI and other MCP servers stay intact.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const Module = require('node:module');
const root = path.resolve(__dirname, '../..');
const cli = path.join(root, '.tools/zcode-3.16.1/agent/zcode.cjs');
const configPath = path.join(root, 'benchmarks/config/evidence-delivery.json');

function patchSource(source, config = JSON.parse(fs.readFileSync(configPath, 'utf8'))) {
  const originalHash = crypto.createHash('sha256').update(source).digest('hex');
  if (originalHash !== config.zcode.upstream_bundle_sha256)
    throw new Error('ZCode bundle changed: review the evidence overlay before execution');
  if (config.zcode.server !== 'rea' || config.zcode.model_result_byte_cap !== null ||
      config.zcode.deduplicate_equal_structured_content !== true)
    throw new Error('Unsupported benchmark evidence-delivery configuration');
  const begin = source.indexOf('function createMcpToolEntry(');
  const end = source.indexOf('\nfunction createMcpProgressEvent(', begin);
  if (begin < 0 || end < 0) throw new Error('ZCode MCP entry anchor missing');
  let entry = source.slice(begin, end);
  const replaceOnce = (from, to) => {
    if (entry.split(from).length !== 2) throw new Error('ZCode evidence overlay anchor is ambiguous');
    entry = entry.replace(from, to);
  };
  replaceOnce('const resultBudget = officialCuaAuthorityVerified ?', `const resultBudget = descriptor2.serverName === "rea" ? {
    maxInlineBytes: Number.MAX_SAFE_INTEGER,
    maxModelBytes: Number.MAX_SAFE_INTEGER,
    strategy: "truncate",
    preview: { direction: "head" }
  } : officialCuaAuthorityVerified ?`);
  replaceOnce('formatModelContent: (output2) => formatMcpToolResult(output2)',
    'formatModelContent: (output2) => descriptor2.serverName === "rea" ? benchmarkReaModelContent(output2) : formatMcpToolResult(output2)');
  const helper = `
function benchmarkReaModelContent(output) {
  // Remove only a proven duplicate. Preserve primary text bytes, unique structured
  // fields, block order, error presentation and all other upstream formatting.
  if (Array.isArray(output?.content) && output.structuredContent !== undefined &&
      output.content.some(block => {
        if (block.type !== "text" || typeof block.text !== "string") return false;
        try { return require("node:util").isDeepStrictEqual(JSON.parse(block.text), output.structuredContent); }
        catch { return false; }
      })) {
    const { structuredContent, ...canonical } = output;
    return formatMcpToolResult(canonical);
  }
  return formatMcpToolResult(output);
}
`;
  const patched = source.slice(0, begin) + helper + entry + source.slice(end);
  return {source: patched, upstream_sha256: originalHash,
    patched_sha256: crypto.createHash('sha256').update(patched).digest('hex'), profile: config.id};
}

// Installed via an explicit --require argument by the benchmark adapter only.
// Retain the original filename so upstream relative imports resolve normally.
if (path.resolve(process.argv[1] || '') === cli) {
  const compile = Module.prototype._compile;
  Module.prototype._compile = function (source, filename, ...args) {
    if (path.resolve(filename) === cli) {
      Module.prototype._compile = compile;
      source = patchSource(source).source;
    }
    return compile.call(this, source, filename, ...args);
  };
}
module.exports = {patchSource, cli, configPath};
