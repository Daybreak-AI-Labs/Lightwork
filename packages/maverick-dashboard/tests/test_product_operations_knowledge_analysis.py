"""Typed operation delegates to exact native goal admission without a provider."""
from types import SimpleNamespace

from starlette.requests import Request


def test_source_analysis_copilot_keeps_exact_source_and_native_operation(monkeypatch):
    from maverick import knowledge_task_context
    from maverick_dashboard import api, auth
    from maverick_dashboard import product_operations_knowledge as operations
    ref={'kind':'dataset','asset_id':'policy','version':'v1','manifest_sha256':'b'*64,'schema':'lightwork.research-registry-ref.v1'}
    row={'key':'c'*64,'reference':ref,'title':'Current policy','purpose':'business'}
    monkeypatch.setattr(operations,'_analysis_catalog',lambda request:('a'*64,[row],{'max_dollars':1,'max_wall_seconds':60}))
    checked=[]
    monkeypatch.setattr(knowledge_task_context,'validate_selection',lambda actor,selection:checked.append((actor,selection)))
    monkeypatch.setattr(auth,'caller_principal',lambda request:'user:alice')
    request=Request({'type':'http','method':'POST','path':'/api/v1/copilot/product-operations/execute','headers':[(b'origin',b'http://testserver')],'query_string':b''})
    arguments={'purpose':'business','source':'c'*64,'title':'Review','description':'Explain'}
    snapshot=operations.analysis_snapshot(request,{})
    assert snapshot.fields[1]['choices_by']['business'][0]['label']=='Current policy / v1'
    preview=operations.analysis_prepare(request,{},arguments)
    assert checked[0][0]=='user:alice'
    assert preview.native['knowledge_context']['references']==[ref]
    calls=[]
    def create(request,payload,background,response):
        calls.append((request.headers['Idempotency-Key'],payload))
        return SimpleNamespace(id=42,status='pending')
    monkeypatch.setattr(api,'create_goal',create)
    outcome=operations.analysis_execute(request,{},arguments,preview.native,'d'*32)
    assert calls[0][0]=='d'*32
    assert calls[0][1].knowledge_context.context=='a'*64
    assert outcome.status=='queued' and outcome.details['goal_id']==42
