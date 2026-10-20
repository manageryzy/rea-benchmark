// Record committed visible messages without the headless projection's 8 KiB cap.
// This observer adds no model tool and never captures discarded attempts.
import { appendFileSync, writeFileSync } from 'node:fs';

export const name = 'benchmark-lossless-output';

export function apply(ctx, config) {
  writeFileSync(config.path, '', { flag: 'wx' });
  const sessions = new Map();
  ctx.on('agent/created', ({ agent }) => {
    sessions.set(agent.session, { id: agent.id, step: null, turn: null });
  });
  ctx.on('session/event', (session, event) => {
    const state = sessions.get(session);
    if (!state) return;
    if (event.type === 'step/start') {
      state.step = event.data.step;
      state.turn = event.data.turn;
    }
    if (event.type !== 'assistant/message') return;
    const blocks = event.data.message.content.filter(block => block.type === 'text')
      .map(block => block.text);
    if (!blocks.length) return;
    appendFileSync(config.path, JSON.stringify({ session_id: state.id, step: state.step, turn: state.turn,
      type: 'committed_assistant_text', blocks, text: blocks.join('') }) + '\n');
  });
}
