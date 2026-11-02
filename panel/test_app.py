import os,tempfile,unittest,asyncio,json
from unittest.mock import patch
from pathlib import Path
temp=tempfile.TemporaryDirectory()
os.environ['RESEARCH_DB']=str(Path(temp.name)/'test.sqlite3')
os.environ['RESEARCH_KEY_FILE']=str(Path(temp.name)/'test.dpapi')
os.environ.pop('DIFY_API_KEY',None)
import httpx
import app as server

class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://127.0.0.1:8765')
    async def asyncTearDown(self):await self.client.aclose()
    async def test_task_persists_and_secret_not_exposed(self):
        payload={'title':'test research','version':'test','inputs':{'question':'q','use_case':'case','candidates':'A\nB','deployment':'self','must_haves':'quote','materials':''}}
        r=await self.client.post('/api/runs',json=payload);self.assertEqual(r.status_code,201)
        i=r.json()['id'];saved=(await self.client.get('/api/runs/'+i)).json()
        self.assertEqual(saved['inputs']['candidates'],'A\nB');self.assertEqual(saved['status'],'draft')
        self.assertEqual((await self.client.post('/api/runs/'+i+'/execute',json={})).status_code,409)
        config=(await self.client.get('/api/config')).json();self.assertFalse(config['api_configured']);self.assertNotIn('api_key',config)
        self.assertEqual((await self.client.post('/api/runs',json={**payload,'inputs':{}})).status_code,422)
    async def test_import_review_and_export(self):
        out={'report':'needs review','run_status':'needs_review','facts':'{"facts":[]}','evidence_json':'{"sources":[]}'}
        r=await self.client.post('/api/import',json={'title':'real import','version':'v6','outputs':out,'events':[{'title':'实际步骤','status':'succeeded','elapsed':2}]})
        self.assertEqual(r.status_code,201);i=r.json()['id'];self.assertEqual(r.json()['origin'],'imported')
        self.assertEqual(r.json()['events'][0]['title'],'实际步骤')
        review=await self.client.post('/api/runs/'+i+'/review',json={'decision':'changes_requested','notes':'Need original sources.'})
        self.assertEqual(review.json()['status'],'needs_review');self.assertEqual(review.json()['human_review']['notes'],'Need original sources.')
        exported=await self.client.get('/api/runs/'+i+'/export')
        self.assertEqual(exported.json()['outputs'],out);self.assertEqual(exported.json()['human_review']['decision'],'changes_requested')
    async def test_malformed_and_cross_origin(self):
        self.assertEqual((await self.client.post('/api/import',json={'x':1})).status_code,422)
        self.assertEqual((await self.client.post('/api/import',json={'report':'x','api_key':'test'})).status_code,422)
        self.assertEqual((await self.client.post('/api/import',json={},headers={'Origin':'https://evil.example'})).status_code,403)
        self.assertEqual((await self.client.get('/api/runs/not-present')).status_code,404)
    async def test_stream_terminal_and_failure(self):
        s={'events':[],'terminal':False}
        server.consume_event({'event':'node_finished','data':{'title':'quote validation','status':'succeeded','elapsed_time':1}},s)
        self.assertEqual(s['events'][0]['title'],'quote validation')
        server.consume_event({'event':'workflow_finished','data':{'status':'succeeded','outputs':{'run_status':'needs_review'},'total_tokens':50}},s)
        self.assertTrue(s['terminal']);self.assertEqual(s['outputs']['run_status'],'needs_review')
        server.consume_event({'event':'error','message':'provider quota exceeded'},s)
        self.assertEqual(s['execution_status'],'failed')
        self.assertEqual(server.clean_error(None),'')
    async def test_partial_success_keeps_outputs(self):
        run=server.insert('partial protocol test','test',{'question':'q'})
        class FakeResponse:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def raise_for_status(self):pass
            async def aiter_lines(self):
                yield 'data: '+json.dumps({'event':'workflow_finished','data':{'status':'partial-succeeded','outputs':{'run_status':'needs_review','report':'needs review'},'elapsed_time':2,'total_tokens':5,'error':None}})
        class FakeClient:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def stream(self,*a,**kw):return FakeResponse()
        with patch.object(server.httpx,'AsyncClient',FakeClient):await server.execute(run['id'])
        saved=server.get_run(run['id'])
        self.assertEqual(saved['status'],'needs_review')
        self.assertEqual(saved['outputs']['_execution_status'],'partial-succeeded')
        self.assertEqual(saved['tokens'],5)
    async def test_edit_draft_and_reject_placeholder(self):
        body={'title':'Venchi draft','version':'v6','inputs':{'question':'ingredients','use_case':'shopping','candidates':'A：完整商品名称＋净含量','deployment':'same market'}}
        created=(await self.client.post('/api/runs',json=body)).json()
        edited=await self.client.post('/api/runs/'+created['id']+'/update',json={**body,'title':'Edited draft'})
        self.assertEqual(edited.status_code,200)
        old=server.KEY
        try:
            server.KEY='app-test-not-a-real-credential'
            rejected=await self.client.post('/api/runs/'+created['id']+'/execute',json={})
            self.assertEqual(rejected.status_code,422)
        finally:server.KEY=old
        self.assertEqual(server.get_run(created['id'])['status'],'draft')

if __name__=='__main__':unittest.main()
