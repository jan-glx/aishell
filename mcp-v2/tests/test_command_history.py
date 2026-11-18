"""Run as root: python3 tests/test_command_history.py."""
import os,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from command_history import CommandHistory
from executor import Identity,dispatch

if os.geteuid() != 0:
    raise SystemExit('Requires root to test the privileged command executor')

with tempfile.TemporaryDirectory() as d:
    db = CommandHistory(Path(d) / 'commands.sqlite3')
    a = SimpleNamespace(subject='user:a', username='a')
    b = SimpleNamespace(subject='user:b', username='b')
    id_a = db.start(a, 'printf hello')
    id_b = db.start(b, 'printf private')
    db.append(id_a, 'stdout', 'hello')
    db.append(id_b, 'stdout', 'private')
    assert len(db.read('user:a')['commands']) == 1
    assert 'private' not in str(db.read('user:a'))
    db.append(id_a, 'stderr', 'z'*12500)
    item = db.read('user:a')['commands'][0]
    assert item['display_clipped']
    assert sum(len(c['content']) for c in item['chunks']) == 10000
    db.finish(id_a, 0)
    assert db.read('user:a')['commands'][0]['exit_code'] == 0

    user = Identity('user:root-test', 'root', 0, 0, Path('/root'),
                    frozenset({'aishell:control'}), True)
    results = []
    def worker():
        results.append(dispatch(None,user,'runCommand',
            {'command':'printf before; sleep 1; printf after'},db))
    t=threading.Thread(target=worker)
    t.start()
    time.sleep(.45)
    live=db.read('user:root-test')['commands'][0]
    assert live['ended'] is None
    assert live['command']=='printf before; sleep 1; printf after'
    assert 'before' in ''.join(c['content'] for c in live['chunks'])
    assert 'after' not in ''.join(c['content'] for c in live['chunks'])
    t.join(5)
    assert results[0]['stdout']=='beforeafter'
    assert db.read('user:root-test')['commands'][0]['exit_code']==0
print('PASS: per-subject isolation, clipping, live input and incremental output')
