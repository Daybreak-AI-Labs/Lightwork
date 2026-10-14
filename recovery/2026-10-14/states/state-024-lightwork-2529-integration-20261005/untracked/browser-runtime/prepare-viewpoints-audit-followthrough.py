from pathlib import Path
root=Path(__file__).resolve().parent.parent
old=(root/'.browser-runtime/viewpoints_server.py').read_text(encoding='utf-8')
old=old.replace('8912','8914')
needle="    import uvicorn\n"
extra='''    from maverick import audit
    audit_state={'action':''}
    actual_audit=audit.record
    def guarded_audit(kind,*args,**kwargs):
        if kwargs.get('record_type')=='viewpoints_request' and kwargs.get('action')==audit_state['action']:
            return False
        return actual_audit(kind,*args,**kwargs)
    audit.record=guarded_audit
    @app.post('/__browser_fixture/viewpoints/audit-fault')
    async def fault(request:Request):
        audit_state['action']=(await request.json()).get('action','')
        return audit_state
    @app.post('/__browser_fixture/viewpoints/history-seed')
    def history_seed(request:Request):
        with tenant_scope(tenant=tenant):
            prepared=native.prepare(request,{'question':'Fixture historical planning question','rounds':1})
            for index in range(45):
                operation=secrets.token_hex(16)
                value=json.loads(json.dumps(prepared));value['body']['question']=f'Retained planning question {index+1}'
                native.records().create({'id':'VPR-'+operation,'owner':value['state']['owner'],'tenant':tenant,'context':value['state']['context'],'binding':'fixture-history-binding','state':'admitting','payload':native.seal({'prepared':value})},action='fixture.history',actor=owner,max_records=1000)
            return native.history(request)
'''
assert needle in old
(root/'.browser-runtime/viewpoints-audit-followthrough-server.py').write_text(old.replace(needle,extra+needle),encoding='utf-8')
print('Prepared genuine audit-outbox failure and full retained history qualification fixture.')
