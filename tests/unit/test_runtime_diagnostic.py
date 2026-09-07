"""Diagnostic output is bounded metadata, never a raw account/server transcript."""

import sys
from pathlib import Path

from factory.agent.runtime_diagnostic import diagnose


def test_diagnostic_reports_model_support_without_disclosing_account_data(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text("""import json, sys
if '--version' in sys.argv:
 print('codex-cli test'); sys.exit(0)
if 'generate-json-schema' in sys.argv:
 from pathlib import Path
 p=Path(sys.argv[sys.argv.index('--out')+1]); p.mkdir(exist_ok=True)
 (p/'ClientRequest.json').write_text(json.dumps({'oneOf':[{'properties':{'method':{'enum':['account/read']}}}]}));sys.exit(0)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 m=r['method']
 if m=='initialize': result={'userAgent':'test', 'private':'DO-NOT-RETAIN'}
 elif m=='model/list': result={'data':[{'model':'gpt-6-astra','supportedReasoningEfforts':[{'reasoningEffort':'high'}],'description':'DO-NOT-RETAIN'}], 'nextCursor':None}
 elif m=='account/read':
  assert r['params']=={'refreshToken':False}
  result={'account':{'type':'chatgpt','planType':'pro','email':'DO-NOT-RETAIN','accessToken':'DO-NOT-RETAIN'},'requiresOpenaiAuth':True}
 else: raise AssertionError(m)
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
""")
    result = diagnose([sys.executable, str(server)], model="gpt-6-astra", effort="high")
    assert result["status"] == "supported"
    assert result["inference_attempted"] is False
    assert result["account"] == {"type": "chatgpt", "plan": "pro", "requires_auth": True}
    assert "DO-NOT-RETAIN" not in str(result)


def test_diagnostic_rejects_untrusted_classification_fields(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text("""import json, sys
if '--version' in sys.argv:
 print('codex-cli test'); sys.exit(0)
if 'generate-json-schema' in sys.argv:
 from pathlib import Path
 p=Path(sys.argv[sys.argv.index('--out')+1]); p.mkdir(exist_ok=True)
 (p/'ClientRequest.json').write_text(json.dumps({'oneOf':[{'properties':{'method':{'enum':['account/read']}}}]}));sys.exit(0)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 result={}
 if r['method']=='model/list': result={'data':[], 'nextCursor':None}
 if r['method']=='account/read': result={'account':{'type':'private-credential','planType':'private-credential'},'requiresOpenaiAuth':'private-credential'}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
""")
    result = diagnose([sys.executable, str(server)])
    assert result["status"] == "model-not-advertised"
    assert result["account"] == {"type": "unknown", "plan": "unknown", "requires_auth": None}
    assert "private-credential" not in str(result)


def test_diagnostic_bounds_repeated_catalogue_cursor(tmp_path: Path) -> None:
    import pytest

    from factory.agent.runtime_diagnostic import DiagnosticError

    server = tmp_path / "server.py"
    server.write_text("""import json, sys
if '--version' in sys.argv:
 print('codex-cli test'); sys.exit(0)
if 'generate-json-schema' in sys.argv:
 from pathlib import Path
 p=Path(sys.argv[sys.argv.index('--out')+1]); p.mkdir(exist_ok=True)
 (p/'ClientRequest.json').write_text(json.dumps({'oneOf':[{'properties':{'method':{'enum':['account/read']}}}]}));sys.exit(0)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 result={'data':[], 'nextCursor':'same'} if r['method']=='model/list' else {}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
""")
    with pytest.raises(DiagnosticError, match="runtime-pagination-invalid"):
        diagnose([sys.executable, str(server)])


def test_unsupported_account_method_does_not_discard_catalogue(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text("""import json, sys
if '--version' in sys.argv:
 print('codex-cli test'); sys.exit(0)
if 'generate-json-schema' in sys.argv:
 from pathlib import Path
 (Path(sys.argv[sys.argv.index('--out')+1])/'ClientRequest.json').write_text('{"oneOf":[]}');sys.exit(0)
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 assert r['method']!='account/read'
 result={'data':[], 'nextCursor':None} if r['method']=='model/list' else {}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
""")
    result = diagnose([sys.executable, str(server)])
    assert result["status"] == "model-not-advertised"
    assert result["account_status"] == "unsupported"
