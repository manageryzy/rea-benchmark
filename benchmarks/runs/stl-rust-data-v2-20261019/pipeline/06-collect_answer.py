"""Keep all visible assistant text, joining stream chunks without altering code."""
import json
from pathlib import Path

def collect(run_dir):
    run_dir = Path(run_dir)
    events = [json.loads(line) for line in (run_dir / 'events.jsonl').read_text().splitlines() if line.strip()]
    messages = []
    chunks = []
    final = ''
    def flush():
        if chunks:
            messages.append(''.join(chunks))
            chunks.clear()
    for event in events:
        if event['type'] == 'status' and event.get('phase') in ('step_start', 'step_end'):
            flush()
        elif event['type'] == 'text':
            chunks.append(event.get('text', ''))
        elif event['type'] == 'final':
            final = event.get('text', '')
    flush()
    lossless_path = run_dir / 'assistant-messages.jsonl'
    source = 'Committed assistant messages without the headless 8 KiB projection cap'
    if lossless_path.exists():
        committed = [json.loads(line) for line in lossless_path.read_text().splitlines() if line.strip()]
        visible = '\n\n'.join(message['text'] for message in committed)
    else:
        source = 'Visible assistant projection, with a complete final snapshot replacing its truncated terminal prefix'
        # The terminal snapshot is lossless. The normal JSON projection caps
        # text at 8 KiB, so appending that snapshot duplicates an open code fence.
        if final and messages and final.startswith(messages[-1]):
            messages[-1] = final
        visible = '\n\n'.join(messages)
    if not visible and not lossless_path.exists():
        visible = final
    elif final and not lossless_path.exists() and not visible.endswith(final):
        # A provider may emit only a final snapshot, without streaming its tail.
        visible += '\n\n' + final
    (run_dir / 'answer-final.md').write_text(final)
    (run_dir / 'answer.md').write_text(visible)
    receipt_path = run_dir / 'result.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        receipt['answer_capture'] = source + '; no reasoning or tool outputs; terminal snapshot preserved separately'
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    return visible

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir')
    args = parser.parse_args()
    collect(args.run_dir)
