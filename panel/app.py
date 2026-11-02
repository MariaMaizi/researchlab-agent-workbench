"""Local research run console. Credentials stay in the server process environment."""
from pathlib import Path
import asyncio,json,os,re,sqlite3,time,uuid
from contextlib import asynccontextmanager,contextmanager
from datetime import datetime,timezone
import httpx
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,Field,SecretStr
import credentials

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('RESEARCH_DB',str(ROOT/'data/runs.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)
KEY=os.environ.get('DIFY_API_KEY','') or credentials.load()
BASE=os.environ.get('DIFY_API_BASE','https://api.dify.ai/v1').rstrip('/')
TASK_KEYS=('question','materials','use_case','candidates','deployment','must_haves')

def now():return datetime.now(timezone.utc).isoformat()
@contextmanager
def connect():
    c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback();raise
    finally:c.close()
def init():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,title TEXT,version TEXT,status TEXT,created_at TEXT,updated_at TEXT,inputs TEXT,outputs TEXT,events TEXT,elapsed REAL,tokens INTEGER,error TEXT,origin TEXT,parent_id TEXT,human_review TEXT)')
        c.execute("UPDATE runs SET status='interrupted',error='服务重启，运行状态待核对；未自动重试，避免重复消耗。' WHERE status='running'")
init()

def decode(row):
    if not row:return None
    d=dict(row)
    for k in ('inputs','outputs','events','human_review'):d[k]=json.loads(d[k] or ('[]' if k=='events' else '{}'))
    return d
def get_run(i):
    with connect() as c:r=c.execute('SELECT * FROM runs WHERE id=?',(i,)).fetchone()
    if not r:raise HTTPException(404,'任务不存在')
    return decode(r)
def update(i,**fields):
    fields['updated_at']=now()
    with connect() as c:c.execute('UPDATE runs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),i])
def clean_error(message):
    if message is None:return ''
    s=str(message)
    if KEY:s=s.replace(KEY,'[REDACTED]')
    return re.sub(r'(?:sk-|app-)[A-Za-z0-9_-]{16,}','[REDACTED]',s)[:1000]
def insert(title,version,inputs,outputs=None,origin='local',parent_id='',elapsed=0,tokens=0):
    i=uuid.uuid4().hex[:12];out=outputs or {}
    status=out.get('run_status','imported_pending_review') if outputs is not None else 'draft'
    if outputs is not None and status not in ('model_review_passed','needs_review','insufficient_evidence','imported_pending_review'):
        status='imported_pending_review'
    with connect() as c:c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (i,title,version,status,now(),now(),json.dumps(inputs,ensure_ascii=False),json.dumps(out,ensure_ascii=False),
         '[]',elapsed,tokens,'',origin,parent_id,'{}'))
    return get_run(i)
def parsed(value,default):
    if isinstance(value,str):
        try:return json.loads(value)
        except (ValueError,TypeError):return default
    return value if value is not None else default

class NewRun(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    version:str=Field(default='v6',max_length=60)
    inputs:dict[str,str]
    parent_id:str=''
class Review(BaseModel):
    decision:str
    notes:str=Field(max_length=5000)
class Connection(BaseModel):
    api_key:SecretStr

workers=set()
@asynccontextmanager
async def lifespan(app):
    yield
    for task in list(workers):task.cancel()
    if workers:await asyncio.gather(*workers,return_exceptions=True)
app=FastAPI(title='选型研究台',lifespan=lifespan,docs_url=None,redoc_url=None)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost'])

@app.middleware('http')
async def local_boundary(request:Request,call_next):
    origin=request.headers.get('origin','')
    if request.method not in ('GET','HEAD') and origin and not re.fullmatch(r'https?://(?:127\.0\.0\.1|localhost)(?::\d+)?',origin):
        return JSONResponse({'detail':'仅接受本机页面提交'},status_code=403)
    if int(request.headers.get('content-length','0') or 0)>4_000_000:
        return JSONResponse({'detail':'文件最多4MB'},status_code=413)
    res=await call_next(request)
    res.headers['X-Content-Type-Options']='nosniff'
    res.headers['X-Frame-Options']='DENY'
    res.headers['Referrer-Policy']='no-referrer'
    return res

@app.get('/api/config')
def config():return {'api_configured':bool(KEY),'storage':'SQLite 本机持久保存','mode':'local',
 'execution_note':'未配置应用API；当前可保存任务、导入真实运行结果和人工复核。' if not KEY else '应用API已配置；发起运行将消耗提供商额度。'}
@app.post('/api/connection')
def configure_connection(body:Connection):
    global KEY
    if workers:raise HTTPException(409,'仍有任务运行，请结束后再修改连接。')
    key=body.api_key.get_secret_value().strip()
    if not re.fullmatch(r'app-[A-Za-z0-9_-]{16,200}',key):raise HTTPException(422,'请填写Dify应用API密钥，不是模型提供商密钥。')
    try:credentials.save(key)
    except Exception:raise HTTPException(500,'本机加密保存失败，尚未更新连接。')
    KEY=key
    return {'api_configured':True,'storage':'Windows当前用户加密存储','key_returned':False}
@app.get('/api/runs')
def runs():
    with connect() as c:rows=c.execute('SELECT * FROM runs ORDER BY created_at DESC').fetchall()
    return [decode(r) for r in rows]
@app.post('/api/runs',status_code=201)
def create(body:NewRun):
    inputs={k:body.inputs.get(k,'').strip() for k in TASK_KEYS}
    for k in ('question','use_case','candidates','deployment'):
        if not inputs[k]:raise HTTPException(422,'请填写 '+k)
    if any(len(v)>20000 for v in inputs.values()):raise HTTPException(422,'输入过长')
    if body.parent_id:get_run(body.parent_id)
    return insert(body.title,body.version,inputs,parent_id=body.parent_id)
@app.get('/api/runs/{i}')
def detail(i:str):return get_run(i)
@app.post('/api/runs/{i}/update')
def edit_draft(i:str,body:NewRun):
    if get_run(i)['status']!='draft':raise HTTPException(409,'仅可编辑尚未运行的草稿；已运行任务请复制复测。')
    inputs={k:body.inputs.get(k,'').strip() for k in TASK_KEYS}
    if any(not inputs[k] for k in ('question','use_case','candidates','deployment')):
        raise HTTPException(422,'请完整填写研究问题、用途、候选和范围要求。')
    update(i,title=body.title,version=body.version,inputs=json.dumps(inputs,ensure_ascii=False))
    return get_run(i)
@app.get('/api/runs/{i}/export')
def export(i:str):
    return Response(json.dumps(get_run(i),ensure_ascii=False,indent=2),media_type='application/json',
      headers={'Content-Disposition':f'attachment; filename="research-{i}.json"'})
@app.post('/api/import',status_code=201)
async def import_result(request:Request):
    data=await request.json()
    if not isinstance(data,dict):raise HTTPException(422,'需要JSON对象')
    # Accept console exports, service API results, or this console's own exports.
    nested=data.get('data',data)
    if not isinstance(nested,dict):raise HTTPException(422,'data必须是JSON对象')
    out=nested.get('outputs',nested)
    if not isinstance(out,dict) or not any(k in out for k in ('report','draft_report','evidence_json')):
        raise HTTPException(422,'没有找到Dify输出字段，请导入包含report/evidence_json的JSON。')
    if any(re.search(r'(?i)(api[_-]?key|authorization|secret|password)',str(k)) for k in out):
        raise HTTPException(422,'导入结果含凭据字段，请只导入工作流输出。')
    task=parsed(out.get('task_json'),{})
    inputs=data.get('inputs') or {k:task.get(k,'') for k in TASK_KEYS}
    if not isinstance(inputs,dict):raise HTTPException(422,'inputs必须为对象')
    try:
        elapsed=float(data.get('elapsed',nested.get('elapsed_time',0)) or 0)
        tokens=int(data.get('tokens',nested.get('total_tokens',0)) or 0)
        if not 0<=elapsed<1e8 or not 0<=tokens<1e10:raise ValueError()
    except (ValueError,TypeError):raise HTTPException(422,'耗时或token格式不正确')
    result=insert(str(data.get('title','导入的研究运行'))[:120],str(data.get('version','unknown'))[:60],inputs,out,
        origin='imported',elapsed=elapsed,tokens=tokens)
    events=data.get('events',[])
    if isinstance(events,list):
        safe_events=[{k:e.get(k) for k in ('at','event','title','status','elapsed','error')} for e in events[:200] if isinstance(e,dict)]
        update(result['id'],events=json.dumps(safe_events,ensure_ascii=False))
    return get_run(result['id'])
@app.get('/api/validation')
def validation():
    records=[]
    for version in ('v5','v6'):
        p=ROOT.parent/version/'本地验收结果.json'
        if p.exists():
            d=json.loads(p.read_text(encoding='utf-8'));records.append({'version':version,'passed':d['passed'],'count':d['count'],'type':'本地结构与边界测试','cloud_runtime_verified':False})
    return {'records':records,'note':'本地断言数量不是业务准确率。后端测试和云端运行另有记录。'}
@app.post('/api/runs/{i}/review')
def human_review(i:str,body:Review):
    get_run(i)
    if body.decision not in ('confirmed','changes_requested'):raise HTTPException(422,'不支持的人工复核状态')
    if not body.notes.strip():raise HTTPException(422,'请记录复核依据')
    update(i,human_review=json.dumps({'decision':body.decision,'notes':body.notes,'at':now()},ensure_ascii=False))
    return get_run(i)

def consume_event(event,state):
    kind=event.get('event');d=event.get('data') or {}
    if kind in ('node_started','node_finished'):
        state['events'].append({'at':now(),'event':kind,'title':str(d.get('title','步骤'))[:100],
            'status':d.get('status','running'),'elapsed':d.get('elapsed_time',0),'error':clean_error(d.get('error',''))})
    if kind=='workflow_finished':
        state.update(terminal=True,outputs=d.get('outputs') or {},elapsed=d.get('elapsed_time',0),
                     tokens=d.get('total_tokens',0),error=clean_error(d.get('error','')),
                     execution_status=d.get('status','failed'))
    if kind=='error':
        state.update(terminal=True,error=clean_error(event.get('message','上游运行错误')),execution_status='failed')
    return state

async def execute(i):
    run=get_run(i);s={'events':[],'terminal':False,'execution_status':'running'};started=time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600,connect=20),follow_redirects=False) as client:
            async with client.stream('POST',BASE+'/workflows/run',headers={'Authorization':'Bearer '+KEY},
                json={'inputs':run['inputs'],'response_mode':'streaming','user':'local-research-console'}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith('data:'):continue
                    consume_event(json.loads(line[5:].strip()),s)
                    update(i,events=json.dumps(s['events'],ensure_ascii=False),elapsed=time.monotonic()-started)
                    if s['terminal']:break
        if not s['terminal']:raise RuntimeError('连接结束但未收到最终运行状态；未自动重试，请先核对Dify运行记录。')
        if s['execution_status'] not in ('succeeded','success','partial-succeeded','partial_success'):raise RuntimeError(s.get('error') or 'Dify流程执行失败')
        out=s.get('outputs',{});out['_execution_status']=s['execution_status'];status=out.get('run_status','imported_pending_review')
        if status not in ('model_review_passed','needs_review','insufficient_evidence'):status='imported_pending_review'
        update(i,status=status,outputs=json.dumps(out,ensure_ascii=False),elapsed=s.get('elapsed') or time.monotonic()-started,
            tokens=s.get('tokens') or 0,error='')
    except asyncio.CancelledError:
        update(i,status='interrupted',error='本地服务停止，未自动重试；请核对Dify记录。');raise
    except Exception as e:update(i,status='failed',error=clean_error(e),elapsed=time.monotonic()-started)

@app.post('/api/runs/{i}/execute')
async def start(i:str):
    if not KEY:raise HTTPException(409,'尚未配置DIFY_API_KEY。可先导入网页运行结果；密钥仅在后端环境中配置。')
    current=get_run(i)
    if re.search(r'完整商品名称|粘贴\s*[ABＡＢ]|候选原文中的名称',current['inputs'].get('candidates','')):
        raise HTTPException(422,'候选仍是示例占位文字，请填写真实商品名称、规格或产品名称后再运行。')
    with connect() as c:
        changed=c.execute("UPDATE runs SET status='running',events='[]',error='',outputs='{}' WHERE id=? AND status='draft'",(i,)).rowcount
    if not changed:raise HTTPException(409,'该任务已运行；请复制为新任务，避免重复消耗。')
    task=asyncio.create_task(execute(i));workers.add(task);task.add_done_callback(workers.discard)
    return get_run(i)

@app.get('/')
def home():return FileResponse(ROOT/'static/index.html')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
