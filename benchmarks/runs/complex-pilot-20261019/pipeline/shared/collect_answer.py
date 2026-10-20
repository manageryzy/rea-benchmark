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
    visible = '\n\n'.join(messages)
    if not visible:
        visible = final
    elif final and not visible.endswith(final):
        # A provider may emit only a final snapshot, without streaming its tail.
        visible += '\n\n' + final
    (run_dir / 'answer-final.md').write_text(final)
    (run_dir / 'answer.md').write_text(visible)
    receipt_path = run_dir / 'result.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        receipt['answer_capture'] = 'All visible assistant text, excluding reasoning and tool outputs; original terminal final preserved in answer-final.md'
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    return visible

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir')
    args = parser.parse_args()
    collect(args.run_dir)
