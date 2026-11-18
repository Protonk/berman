"""Run from the fixture checkout after updating answer.py."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from answer import VALUE

if VALUE != Path('challenge.txt').read_text().strip():
    raise SystemExit('answer.py must contain the checkout value')

result = uuid4().hex
digest = hashlib.sha256(Path('answer.py').read_bytes()).hexdigest()
Path('test-result.json').write_text(json.dumps({'answer_sha256': digest, 'code': result}) + '\n')
print('passed', result)
