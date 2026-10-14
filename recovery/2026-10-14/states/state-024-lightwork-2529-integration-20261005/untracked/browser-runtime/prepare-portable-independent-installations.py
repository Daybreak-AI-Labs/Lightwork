from pathlib import Path
root=Path(__file__).resolve().parent.parent
s=(root/'.browser-runtime/competence_transport_server.py').read_text(encoding='utf-8')
s=s.replace("    with tenant_scope(tenant=tenant):\n", "    with tenant_scope(tenant=tenant):\n",1)
start=s.index('    with tenant_scope(tenant=tenant):\n')
end=s.index('    native_run=runner.run_flow',start)
block='''    installation_role=os.environ['TASK_PORTABLE_INSTALLATION']
    assert installation_role in {'source','target'}
    identity,name,owner,field,minimum,factor=(('private-source','Share a full allowance rule','user:browser-reviewer','private_amount',999999999998,1000000000000) if installation_role=='source' else ('local-target','Check our local allowance','user:browser-learning-reviewer','local_amount',999999999997,999999999999))
    with tenant_scope(tenant=tenant):
        spec={'inputs':{field:{'type':'integer','min':minimum,'max':minimum+2}},'output_type':'integer','constants':[factor]}
        artifact=synthesize(spec,[{'inputs':{field:i},'output':i*factor} for i in range(minimum,minimum+3)])
        flow=Flow(id=identity,name=name,owner=owner,start='answer',nodes={'answer':FlowNode(id='answer',kind='agent',brief='Calculate the independently entered local allowance',label='Local allowance',output='allowance',learned_fallback=False)})
        flow=store.save_flow(attach_learned_operator(flow,'answer',artifact));store.publish_flow(flow.id,expected_version=flow.version,expected_revision=flow.revision)
        published,publication=store.load_published_bundle(flow.id);runs=[]
        for i in (minimum,minimum+2):
            values={field:i};pending=store.FlowRun(run_id=store.new_run_id(),flow_id=flow.id,status='queued',owner=owner,input_data=values,data=values,definition_digest=publication['definition_digest'],definition_version=published.version,definition_revision=published.revision,subflow_digests=publication['subflow_digests'],release_digest=publication['release_digest'],release_id=publication['release_id'])
            store.save_run(pending);done=execution.execute(published,owner=owner,run_id=pending.run_id,data=values,agent_runner=refuse,action_runner=refuse);assert done.status=='completed',done.error;runs.append(done.run_id)
        fixtures[identity]={'flow':flow.id,'name':name,'field':field,'owner':owner,'runs':runs,'source_programs':len(artifact['candidates']),'minimum':minimum,'factor':factor}
    projection_equal=True
'''
s=s[:start]+block+s[end:]
s=s.replace("port=8903", "port=8915 if installation_role=='source' else 8916")
(root/'.browser-runtime/portable-independent-installations-server.py').write_text(s,encoding='utf-8')
print('Prepared two real independent installations with distinct storage/keys/owners/domains/local constants; unchanged native learner/executor.')
